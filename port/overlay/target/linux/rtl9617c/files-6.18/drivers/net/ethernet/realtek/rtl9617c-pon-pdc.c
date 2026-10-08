// SPDX-License-Identifier: GPL-2.0-or-later
/* Native PON downstream map; GPL aal_pdc.c and software CPU forwarding. */
#include <linux/bitfield.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include "rtl9617c-pon.h"

#define PDC_COUNTER 0x9000
#define PDC_CTRL 0x9014
#define PDC_ACCESS 0x9020
#define PDC_DATA1 0x9024
#define PDC_DATA0 0x9028

static int rtl_pdc_access(struct rtl9617c_pon *pon, unsigned int index, bool write)
{
	u32 status;
	writel(BIT(31) | (write ? BIT(30) : 0) | index, pon->mac + PDC_ACCESS);
	return readl_poll_timeout(pon->mac + PDC_ACCESS, status, !(status & BIT(31)), 1, 100000);
}

int rtl_pon_pdc_configure_locked(struct rtl9617c_pon *pon)
{
	unsigned int index;
	int ret = 0;

	lockdep_assert_held(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (!pon->mac_prepared) {
		ret = -EAGAIN;
		goto unlock;
	}
	/* CRC stays on the wire only; PDC inserts its original 16-byte control header. */
	rtl_pon_update(pon->mac + PDC_CTRL, GENMASK(2, 0) | GENMASK(25, 16), 7);
	for (index = 0; index < 256; index++) {
		/* Logical CPU16, physical PON source7, bypass FE for software forwarding. */
		u32 data0 = (16 << 3) | (7 << 9) | BIT(15);
		if (index & BIT(6))
			data0 |= 7; /* MPCP/OAM classes receive priority7. */
		writel(data0, pon->mac + PDC_DATA0);
		writel(0, pon->mac + PDC_DATA1);
		ret = rtl_pdc_access(pon, index, true);
		if (ret)
			goto unlock;
	}
	rtl_pon_update(pon->mac + PDC_COUNTER, GENMASK(2, 0), 0);
	pon->pdc_configured = true;
	dev_info(pon->dev, "PDC: 256 downstream map entries configured for native CPU16\n");
unlock:
	pon->pdc_error = ret;
	mutex_unlock(&pon->lock);
	return ret;
}

ssize_t rtl_pon_pdc_state(struct rtl9617c_pon *pon, char *buf)
{
	static const unsigned int sample[] = {0, 31, 63, 64, 127, 192, 255};
	ssize_t length;
	unsigned int i;
	int ret;

	length = sysfs_emit(buf, "configured=%u error=%d control=%08x counter_cfg=%08x\n",
		pon->pdc_configured, pon->pdc_error, readl(pon->mac + PDC_CTRL), readl(pon->mac + PDC_COUNTER));
	for (i = 0; i < ARRAY_SIZE(sample); i++) {
		ret = rtl_pdc_access(pon, sample[i], false);
		if (ret)
			return ret;
		length += scnprintf(buf + length, PAGE_SIZE - length,
			"index=%u data=%08x/%08x destination=%u source=%u bypass=%u cos=%u\n",
			sample[i], readl(pon->mac + PDC_DATA0), readl(pon->mac + PDC_DATA1),
			(unsigned int)FIELD_GET(GENMASK(8, 3), readl(pon->mac + PDC_DATA0)),
			(unsigned int)FIELD_GET(GENMASK(14, 9), readl(pon->mac + PDC_DATA0)),
			!!(readl(pon->mac + PDC_DATA0) & BIT(15)), readl(pon->mac + PDC_DATA0) & 7);
	}
	return length;
}
