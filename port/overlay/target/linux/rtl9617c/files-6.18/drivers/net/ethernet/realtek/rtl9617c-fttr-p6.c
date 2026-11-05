// SPDX-License-Identifier: GPL-2.0-only
/* TYFZA1 ca-ne.ko P6 sequences. P6/QX is separate from the copper PHY's S3. */
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/platform_device.h>
#include <linux/reset.h>
#include <linux/unaligned.h>

#include "rtl9617c-fttr-p6.h"

static void p6_update(void __iomem *base, unsigned int reg, u32 mask, u32 value)
{
	writel((readl(base + reg) & ~mask) | value, base + reg);
}

static int p6_flush_stage(struct rtl_fttr_p6 *p6, struct rtl_fttr_queue_state *state,
			 unsigned int control, unsigned int occupancy, u32 saved)
{
	const u32 fields = GENMASK(10, 8) | BIT(15);
	u32 value;
	unsigned int queue;
	int ret = 0;

	for (queue = 0; queue < 8; queue++) {
		p6_update(p6->ni, control, fields, (queue << 8) | BIT(15));
		value = readl(p6->ni + control);
		if ((value & fields) != ((queue << 8) | BIT(15))) {
			ret = -EIO;
			break;
		}
		ret = readl_poll_timeout(p6->ni + occupancy, state->status,
			!(state->status & BIT(16 + queue)), 1, 100000);
		if (ret)
			break;
		state->completed |= BIT(queue);
		p6_update(p6->ni, control, BIT(15), 0);
		if (readl(p6->ni + control) & BIT(15)) {
			ret = -EIO;
			break;
		}
	}
	if (ret)
		state->failed = queue;
	/* Restore COS and flush enable even on a stuck queue, retaining the
	 * primary timeout separately from an unsuccessful control restoration. */
	p6_update(p6->ni, control, fields, saved & fields);
	if ((readl(p6->ni + control) & fields) != (saved & fields))
		state->cleanup_error = -EIO;
	if (!ret) {
		state->status = readl(p6->ni + occupancy);
		if (state->status & GENMASK(23, 16))
			ret = -EAGAIN;
	}
	state->error = ret;
	return ret ? ret : state->cleanup_error;
}

/* Caller serializes NI control and stops producers before these queue stages.
 * ES occupancy does not include DMA prefetch, PE or the peer MiniOLT buffer. */
int rtl_fttr_p6_flush_queues(struct rtl_fttr_p6 *p6)
{
	u32 l3_saved = readl(p6->ni + 0x63fc), l2_saved = readl(p6->ni + 0x234c);
	int ret;

	p6->l3_queue = (struct rtl_fttr_queue_state) { .failed = 0xff };
	p6->l2_queue = (struct rtl_fttr_queue_state) { .failed = 0xff };
	p6->l3_queue.status = readl(p6->ni + 0x683c);
	p6->l2_queue.status = readl(p6->ni + 0x23d8);
	/* Reject either pre-existing owner before changing either stage. */
	if ((l3_saved | l2_saved) & BIT(15)) {
		if (l3_saved & BIT(15))
			p6->l3_queue.error = -EBUSY;
		if (l2_saved & BIT(15))
			p6->l2_queue.error = -EBUSY;
		return -EBUSY;
	}
	/* P6 DEEPQ traverses L3 ES port14 (VOQs112..119) before L2 ES port6.
	 * Both use COS[10:8]/flush[15] and occupancy[23:16].
	 */
	ret = p6_flush_stage(p6, &p6->l3_queue, 0x63fc, 0x683c, l3_saved);
	if (ret)
		return ret;
	ret = p6_flush_stage(p6, &p6->l2_queue, 0x234c, 0x23d8, l2_saved);
	if (ret)
		return ret;
	/* A later producer may have refilled L3 while L2 was being flushed. */
	p6->l3_queue.status = readl(p6->ni + 0x683c);
	p6->l2_queue.status = readl(p6->ni + 0x23d8);
	if (p6->l3_queue.status & GENMASK(23, 16))
		p6->l3_queue.error = -EAGAIN;
	if (p6->l2_queue.status & GENMASK(23, 16))
		p6->l2_queue.error = -EAGAIN;
	return p6->l3_queue.error ? p6->l3_queue.error : p6->l2_queue.error;
}

static u16 p6_ind_read(struct rtl_fttr_p6 *p6, unsigned int reg)
{
	p6_update(p6->qx, 0x4c, BIT(31) | BIT(30) | 0xffff, BIT(31) | reg);
	udelay(1);
	return readl(p6->qx + 0x54);
}

static void p6_ind_write(struct rtl_fttr_p6 *p6, unsigned int reg, u16 value)
{
	p6_update(p6->qx, 0x50, 0xffff, value);
	p6_update(p6->qx, 0x4c, BIT(31) | BIT(30) | 0xffff, BIT(31) | BIT(30) | reg);
	udelay(1);
}

static int p6_profile_validate(const struct firmware *fw)
{
	u32 rows;
	unsigned int i;

	if (fw->size < 12 || memcmp(fw->data, "TP6S", 4) || get_unaligned_le32(fw->data + 4) != 1)
		return -EINVAL;
	rows = get_unaligned_le32(fw->data + 8);
	if (!rows || rows > 512 || fw->size != 12 + rows * 12)
		return -EINVAL;
	for (i = 0; i < rows; i++) {
		const u8 *row = fw->data + 12 + i * 12;
		u32 reg = get_unaligned_le32(row);
		u32 delay = get_unaligned_le32(row + 8);

		if (reg > 0x5c || (reg & 3) || delay > 1000000)
			return -EINVAL;
	}
	return 0;
}

static void p6_profile_load(struct rtl_fttr_p6 *p6, const struct firmware *fw)
{
	unsigned int i, rows = get_unaligned_le32(fw->data + 8);

	for (i = 0; i < rows; i++) {
		const u8 *row = fw->data + 12 + i * 12;
		u32 delay = get_unaligned_le32(row + 8);

		writel(get_unaligned_le32(row + 4), p6->qx + get_unaligned_le32(row));
		if (delay < 1000)
			udelay(delay);
		else
			usleep_range(delay, delay + 100);
	}
}

static void p6_mac_configure(struct rtl_fttr_p6 *p6, bool ten_gig)
{
	void __iomem *ni = p6->ni;
	u32 gige = ten_gig ? 0 : BIT(31);
	u32 interface = ten_gig ? 2 : 1;
	u16 value;

	p6_update(ni, 0x2300, BIT(6), 0);
	p6_update(ni, 0x6108, BIT(14), 0);
	p6_update(ni, 0xa1f4, BIT(20), BIT(20));
	p6_update(ni, 0xb2a8, BIT(31) | GENMASK(29, 28), gige);
	p6_update(ni, 0xb2a8, BIT(31) | GENMASK(29, 28), gige | GENMASK(29, 28));
	msleep(10);
	p6_update(ni, 0xb208, 3, 1);
	p6_update(ni, 0xb20c, 0x1f1f, ten_gig ? 0xa0e : 0xa0a);
	p6_update(ni, 0xb200, 0xe070, 0xc000);
	msleep(10);
	p6_update(ni, 0xb2a8, BIT(31) | GENMASK(29, 28), gige);
	p6_update(ni, 0xb2a8, BIT(31) | GENMASK(29, 28), gige | interface << 28);
	p6_update(ni, 0xb200, 0xe070, 0);
	msleep(10);
	p6_update(ni, 0xb200, 0xe070, 0xc000);
	msleep(10);
	value = p6_ind_read(p6, 0xcd);
	p6_ind_write(p6, 0xcd, value | BIT(3));
	p6_update(ni, 0xb200, 0xe070, 0);
	msleep(10);
	value = p6_ind_read(p6, 0xcd);
	p6_ind_write(p6, 0xcd, value & ~BIT(3));
	p6_update(ni, 0xa1f4, BIT(20), 0);
	p6_update(ni, 0x2300, BIT(6), BIT(6));
	p6_update(ni, 0x6108, BIT(14), BIT(14));
}

void rtl_fttr_p6_stop(struct rtl_fttr_p6 *p6)
{
	p6_update(p6->ni, 0x2300, BIT(6), 0);
	p6_update(p6->ni, 0x6108, BIT(14), 0);
	p6_update(p6->qx, 0, BIT(0), BIT(0));
	p6->configured = false;
}

bool rtl_fttr_p6_link(struct rtl_fttr_p6 *p6)
{
	unsigned int reg = p6->mode == 5 ? 0x3e : 0xa0;
	unsigned int mask = p6->mode == 5 ? BIT(4) : BIT(12);

	/* Original onuinfo checks page1/reg30 for mode5, page5/reg0 for mode9. */
	if (!p6->configured || !(readl(p6->qx + 0x58) & BIT(13)))
		return false;
	p6_ind_read(p6, reg);
	return !!(p6_ind_read(p6, reg) & mask);
}

int rtl_fttr_p6_configure(struct rtl_fttr_p6 *p6, unsigned int mode, bool nway)
{
	const struct firmware *fw;
	const char *name;
	u32 clock;
	u16 value;
	bool ten_gig = mode == 9;
	unsigned int i;
	int ret;

	if (mode != 5 && mode != 9)
		return -EOPNOTSUPP;
	name = ten_gig ? "realtek/rtl9617c/p6_serdes_usxgmii_10g.bin" :
		"realtek/rtl9617c/p6_serdes_sgmii_2g5.bin";
	rtl_fttr_p6_stop(p6);
	ret = request_firmware_direct(&fw, name, p6->dev);
	if (ret)
		goto out;
	ret = p6_profile_validate(fw);
	if (ret)
		goto release;
	ret = reset_control_assert(p6->reset);
	if (ret)
		goto release;
	udelay(1);
	ret = reset_control_deassert(p6->reset);
	if (ret)
		goto release;
	p6_update(p6->qx, 0, BIT(0), BIT(0));
	p6_update(p6->qx, 4, 0x1f, 0x1f);
	msleep(1);
	p6_update(p6->qx, 4, ten_gig ? 0x3ff : 0x1f, ten_gig ? 0xd : 0x12);
	for (i = 0; i < (ten_gig ? 1 : 4); i++)
		p6_update(p6->qx, 8 + i * 4, 0x1ffff, ten_gig ? 0x5cf4 : 0x5cf5);
	if (ten_gig)
		p6_update(p6->qx, 0x18, BIT(1), BIT(1));
	p6_ind_read(p6, 0x405);
	p6_ind_write(p6, 0x405, 0x9f);
	p6_profile_load(p6, fw);
	p6_update(p6->qx, 0, BIT(0), 0);
	ret = readl_poll_timeout(p6->qx + 0x58, clock, clock & BIT(13), 10000, 15000000);
	if (ret)
		goto release;
	msleep(400);
	p6_mac_configure(p6, ten_gig);
	value = p6_ind_read(p6, 2);
	p6_ind_write(p6, 2, nway ? value | BIT(8) : value & ~BIT(8));
	value = p6_ind_read(p6, 0xf1);
	p6_ind_write(p6, 0xf1, nway ? value & ~0xf : value | 0xf);
	/* Original aal_ni_{xge_mac_frame_len,pkt_len}_set register fields. */
	p6_update(p6->ni, 0xb230, 0xffff, RTL_FTTR_FRAME_BYTES + 4);
	if ((readl(p6->ni + 0xa034) & 0x3fff) < RTL_FTTR_FRAME_BYTES + 4) {
		p6_update(p6->ni, 0xa034, 0x3fff, RTL_FTTR_FRAME_BYTES + 4);
		msleep(1);
	}
	p6->mode = mode;
	p6->nway = nway;
	p6->configured = true;
release:
	release_firmware(fw);
out:
	if (ret)
		rtl_fttr_p6_stop(p6);
	p6->error = ret;
	return ret;
}

int rtl_fttr_p6_init(struct platform_device *pdev, struct rtl_fttr_p6 *p6, void __iomem *ni)
{
	struct resource *resource = platform_get_resource_byname(pdev, IORESOURCE_MEM, "fttr-p6");

	if (!resource)
		return 0;
	p6->qx = devm_ioremap_resource(&pdev->dev, resource);
	if (IS_ERR(p6->qx))
		return PTR_ERR(p6->qx);
	p6->reset = devm_reset_control_get_exclusive(&pdev->dev, "fttr-p6");
	if (IS_ERR(p6->reset))
		return PTR_ERR(p6->reset);
	p6->dev = &pdev->dev;
	p6->ni = ni;
	return 0;
}
