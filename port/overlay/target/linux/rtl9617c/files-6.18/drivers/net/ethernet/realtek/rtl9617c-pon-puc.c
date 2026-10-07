// SPDX-License-Identifier: GPL-2.0-or-later
/* Native EPON PUC scheduler and queues, from GPL aal_puc.c. */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-ni.h"

#define PVT_ACCESS 0x8000
#define PVT_DATA0 0x8014
#define VOQ_MODE 0x804c
#define BTC 0x8050
#define REPORT 0x8054
#define REPORT_Q12 0x8078
#define REPORT_Q34 0x807c
#define BM_STATUS 0x8088
#define PUC_CONFIG 0x808c
#define LIMIT_SELECT 0x8090
#define LIMIT_A 0x80d0
#define BACKPRESSURE 0x80e4
#define PUC_IE 0x80f4
#define CONTROL 0x813c
#define CONTROL1 0x8140
#define CONTROL2 0x8144
#define PUC_STATUS 0x8148
#define VALID_VOQ0 0x81bc
#define CIR_ACCESS 0x81c4
#define CIR_DATA1 0x81c8
#define CIR_DATA0 0x81cc
#define PIR_ACCESS 0x81ec
#define PIR_DATA1 0x81f0
#define PIR_DATA0 0x81f4
#define AGGREGATE1 0x81f8
#define AGGREGATE0 0x81fc

static int rtl_puc_access(struct rtl9617c_pon *pon, unsigned int reg,
			  unsigned int index, bool write)
{
	u32 status;
	writel(BIT(31) | (write ? BIT(30) : 0) | index, pon->mac + reg);
	return readl_poll_timeout(pon->mac + reg, status, !(status & BIT(31)), 1, 100000);
}

static int rtl_puc_port(struct rtl9617c_pon *pon, unsigned int port,
			unsigned int queues, bool enabled)
{
	u32 data[5] = {};
	unsigned int cos;

	/* Eight nine-bit queue fields span DATA0..2; zero weights select SP. */
	for (cos = 0; cos < 8; cos++) {
		unsigned int bit = cos * 9;
		u32 queue = enabled && cos < queues ? port * queues + cos : 0;
		u64 field = queue | (enabled && cos < queues ? BIT(8) : 0);

		data[bit / 32] |= field << (bit % 32);
		if (bit % 32 > 23)
			data[bit / 32 + 1] |= field >> (32 - bit % 32);
	}
	if (enabled)
		data[2] |= BIT(12);
	for (cos = 0; cos < 5; cos++)
		writel(data[cos], pon->mac + PVT_DATA0 - cos * 4);
	return rtl_puc_access(pon, PVT_ACCESS, port, true);
}

static int rtl_puc_token_buckets(struct rtl9617c_pon *pon)
{
	unsigned int queue;
	int ret;

	for (queue = 0; queue < 256; queue++) {
		/* CIR disabled; PIR unlimited, as stock EPON initialization. */
		writel(0, pon->mac + CIR_DATA0);
		writel(2 << 6, pon->mac + CIR_DATA1);
		ret = rtl_puc_access(pon, CIR_ACCESS, queue, true);
		if (ret)
			return ret;
		writel(~0U, pon->mac + PIR_DATA0);
		writel(0x3f | (2 << 6), pon->mac + PIR_DATA1);
		ret = rtl_puc_access(pon, PIR_ACCESS, queue, true);
		if (ret)
			return ret;
	}
	return 0;
}

int rtl_pon_puc_configure(struct rtl9617c_pon *pon)
{
	u32 status;
	unsigned int i, ports = pon->llid_count;
	unsigned int queues = ports > 16 ? 4 : 8;
	int ret;

	mutex_lock(&pon->lock);
	if (!pon->mac_prepared) {
		ret = -EAGAIN;
		goto unlock;
	}
	pon->puc_configured = false;
	ret = rtl9617c_ni_report_configure(pon->ni_dev,
		pon->mode == RTL_PON_EPON_BI10G ? 100 : 82, 220);
	if (ret)
		goto unlock;
	rtl_pon_update(pon->control, BIT(8), BIT(8));
	udelay(500);
	ret = readl_poll_timeout(pon->mac + BM_STATUS, status, status & BIT(0), 1, 100000);
	if (ret)
		goto unlock;
	ret = readl_poll_timeout(pon->mac + PUC_STATUS, status, status & BIT(31), 1, 100000);
	if (ret)
		goto unlock;
	writel(0, pon->mac + PUC_IE);
	rtl_pon_update(pon->mac + PUC_CONFIG, GENMASK(1, 0) | GENMASK(18, 16) | BIT(30),
		2 | BIT(30)); /* CRC regenerate, counters retained, invalid queue drop. */
	writel(0x7a0, pon->mac + LIMIT_A);
	writel(0x3b0, pon->mac + LIMIT_A + 4);
	writel(0x200, pon->mac + LIMIT_A + 8);
	for (i = 0; i < 16; i++)
		writel(0x55555555, pon->mac + LIMIT_SELECT + i * 4);
	writel(queues == 4 ? 1 : 0, pon->mac + VOQ_MODE);
	for (i = 0; i < 64; i++) {
		ret = rtl_puc_port(pon, i, queues, i < ports);
		if (ret)
			goto unlock;
	}
	for (i = 0; i < 8; i++) {
		unsigned int active = ports * queues;
		u32 valid = i < active / 32 ? ~0U : i == active / 32 && active % 32 ?
			    GENMASK(active % 32 - 1, 0) : 0;
		/* Queue127 is the stock special control queue. */
		if (i == 3)
			valid |= BIT(31);
		writel(valid, pon->mac + VALID_VOQ0 - i * 4);
	}
	rtl_pon_update(pon->mac + REPORT,
		GENMASK(1, 0) | BIT(24) | BIT(28) | BIT(30) | BIT(31), 1 | BIT(24));
	writel(0xffff8000, pon->mac + REPORT_Q12);
	writel(0x80008000, pon->mac + REPORT_Q34);
	if (pon->mode == RTL_PON_EPON_BI10G) {
		rtl_pon_update(pon->mac + 0x80fc, GENMASK(14, 0), 0x100);
		rtl_pon_update(pon->mac + 0x80f8, BIT(28) | GENMASK(14, 12),
			(pon->llid_count == 1 ? BIT(28) : 0) | (2 << 12));
	}
	rtl_pon_update(pon->mac + BACKPRESSURE, BIT(0) | BIT(4) | GENMASK(30, 16),
		BIT(0) | (0x100 << 16));
	rtl_pon_update(pon->mac + BTC,
		GENMASK(5, 0) | BIT(8) | BIT(12) | BIT(25) | GENMASK(31, 27), 20 | BIT(8));
	writel(pon->mode == RTL_PON_EPON_BI10G ? 0x0a000000 : 0x55550000, pon->mac + 0x8230);
	writel(pon->mode == RTL_PON_EPON_BI10G ? 0xf0001400 : 0xffffaaaa, pon->mac + 0x8234);
	rtl_pon_update(pon->mac + CONTROL, BIT(30) | BIT(26), BIT(30));
	rtl_pon_update(pon->mac + CONTROL1, GENMASK(14, 0), 20 | (20 << 5) | (20 << 10));
	rtl_pon_update(pon->mac + CONTROL2, BIT(26) | GENMASK(4, 0), BIT(26) | 20);
	ret = rtl_puc_token_buckets(pon);
	if (ret)
		goto unlock;
	rtl_pon_update(pon->mac + AGGREGATE0, GENMASK(25, 0), 0);
	rtl_pon_update(pon->mac + AGGREGATE1, GENMASK(11, 0), 0);
	pon->puc_configured = true;
	dev_info(pon->dev, "PUC: %u LLID ports, %u queues each, 256 CIR/PIR entries\n", ports, queues);
unlock:
	pon->puc_error = ret;
	mutex_unlock(&pon->lock);
	return ret;
}

ssize_t rtl_pon_puc_state(struct rtl9617c_pon *pon, char *buf)
{
	static const unsigned int sample[] = {0, 7, 127, 255};
	ssize_t length;
	unsigned int i;
	int ret;

	length = sysfs_emit(buf,
		"configured=%u error=%d bm=%08x status=%08x config=%08x mode=%08x btc=%08x report=%08x thresholds=%08x/%08x limits=%08x/%08x/%08x backpressure=%08x valid=%08x/%08x/%08x/%08x ni_report=%08x\n",
		pon->puc_configured, pon->puc_error, readl(pon->mac + BM_STATUS), readl(pon->mac + PUC_STATUS),
		readl(pon->mac + PUC_CONFIG), readl(pon->mac + VOQ_MODE), readl(pon->mac + BTC),
		readl(pon->mac + REPORT), readl(pon->mac + REPORT_Q12), readl(pon->mac + REPORT_Q34),
		readl(pon->mac + LIMIT_A), readl(pon->mac + LIMIT_A + 4), readl(pon->mac + LIMIT_A + 8),
		readl(pon->mac + BACKPRESSURE), readl(pon->mac + VALID_VOQ0),
		readl(pon->mac + VALID_VOQ0 - 4), readl(pon->mac + VALID_VOQ0 - 8),
		readl(pon->mac + VALID_VOQ0 - 12), rtl9617c_ni_report_state(pon->ni_dev));
	for (i = 0; i < 2; i++) {
		ret = rtl_puc_access(pon, PVT_ACCESS, i, false);
		if (ret)
			return ret;
		length += scnprintf(buf + length, PAGE_SIZE - length, "port%u=%08x/%08x/%08x/%08x/%08x\n",
			i, readl(pon->mac + PVT_DATA0), readl(pon->mac + PVT_DATA0 - 4),
			readl(pon->mac + PVT_DATA0 - 8), readl(pon->mac + PVT_DATA0 - 12),
			readl(pon->mac + PVT_DATA0 - 16));
	}
	for (i = 0; i < ARRAY_SIZE(sample); i++) {
		ret = rtl_puc_access(pon, CIR_ACCESS, sample[i], false);
		if (ret)
			return ret;
		length += scnprintf(buf + length, PAGE_SIZE - length, "cir%u=%08x/%08x ",
			sample[i], readl(pon->mac + CIR_DATA0), readl(pon->mac + CIR_DATA1));
		ret = rtl_puc_access(pon, PIR_ACCESS, sample[i], false);
		if (ret)
			return ret;
		length += scnprintf(buf + length, PAGE_SIZE - length, "pir%u=%08x/%08x\n",
			sample[i], readl(pon->mac + PIR_DATA0), readl(pon->mac + PIR_DATA1));
	}
	return length;
}
