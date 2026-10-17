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
	/* Original __p6_sds_status_get: ck_rdy=QX58[13], link_ok=page1/reg30[4]. */
	return p6->configured && (readl(p6->qx + 0x58) & BIT(13)) &&
		(p6_ind_read(p6, 0x3e) & BIT(4));
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
