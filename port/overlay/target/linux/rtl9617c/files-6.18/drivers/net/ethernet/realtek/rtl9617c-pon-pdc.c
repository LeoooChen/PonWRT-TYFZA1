// SPDX-License-Identifier: GPL-2.0-or-later
/* Native PON downstream map; GPL aal_pdc.c and software CPU forwarding. */
#include <linux/bitfield.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-pon-data.h"

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

int rtl_pon_pdc_refresh(struct rtl9617c_pon *pon)
{
	unsigned int index, bank;
	int ret;

	lockdep_assert_held(&pon->lock);
	if (!pon->pdc_configured)
		return 0;
	for (index = 0; index < 64; index++) {
		u32 source = rtl_pon_data_source(index, readl(pon->mac + 0x64 + index * 4));

		if (pon->pdc_source[index] == source)
			continue;
		for (bank = 0; bank < 2; bank++) {
			writel((pon->fe_enabled?24:16) << 3 | (7 << 9) |
			       (pon->fe_enabled?0:BIT(15)), pon->mac + PDC_DATA0);
			writel((source - 32) << 4, pon->mac + PDC_DATA1);
			ret = rtl_pdc_access(pon, index | (bank << 7), true);
			if (ret) {
				pon->pdc_error = ret;
				pon->pdc_configured = false;
				return ret;
			}
		}
		pon->pdc_source[index] = source;
	}
	return 0;
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
	pon->pdc_configured = false;
	/* CRC stays on the wire only; PDC inserts its original 16-byte control header. */
	rtl_pon_update(pon->mac + PDC_CTRL, GENMASK(2, 0) | GENMASK(25, 16), 7);
	for (index = 0; index < 256; index++) {
		unsigned int entry = index & 63;
		unsigned int source = index & BIT(6) ? 7 :
			rtl_pon_data_source(entry, readl(pon->mac + 0x64 + entry * 4));
		u32 data0 = (16 << 3) | (7 << 9) | BIT(15);

		if (index & BIT(6))
			data0 |= 7;
		else
			pon->pdc_source[entry] = source;
		writel(data0, pon->mac + PDC_DATA0);
		/* Keep the physical PON source; POL_ID carries the LLID without
		 * enabling either hardware policer. Upper CPU source IDs are
		 * normalized by the NI receive fabric on this board. */
		writel(index & BIT(6) ? 0 : (source - 32) << 4, pon->mac + PDC_DATA1);
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

int rtl_pon_fe_configure_locked(struct rtl9617c_pon *pon,bool enable)
{
 unsigned int entry,bank;
 int ret=0;

 lockdep_assert_held(&pon->control_lock);
 mutex_lock(&pon->lock);
 if(enable && (!rtl_pon_mode_is_epon(pon->mode) || !pon->pdc_configured ||
               !pon->puc_configured || !pon->traffic_enabled)) {
  ret=-EAGAIN;
  goto unlock;
 }
 if(enable) {
  ret=rtl9617c_ni_fe_enable(pon->ni_dev);
  if(ret)goto unlock;
 }
 /* OAM/control maps retain CPU16 and their original header container. */
 if(pon->pdc_configured)
  for(entry=0;entry<64;entry++)
   for(bank=0;bank<2;bank++) {
    writel(((enable?24:16)<<3)|(7<<9)|(enable?0:BIT(15)),pon->mac+PDC_DATA0);
    writel((pon->pdc_source[entry]-32)<<4,pon->mac+PDC_DATA1);
    ret=rtl_pdc_access(pon,entry|(bank<<7),true);
    if(ret)goto unlock;
   }
 pon->fe_enabled=enable;
 if(!enable)ret=rtl9617c_ni_fe_disable(pon->ni_dev);
unlock:
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
			"index=%u data=%08x/%08x destination=%u source=%u bypass=%u cos=%u llid=%u\n",
			sample[i], readl(pon->mac + PDC_DATA0), readl(pon->mac + PDC_DATA1),
			(unsigned int)FIELD_GET(GENMASK(8, 3), readl(pon->mac + PDC_DATA0)),
			(unsigned int)FIELD_GET(GENMASK(14, 9), readl(pon->mac + PDC_DATA0)),
			!!(readl(pon->mac + PDC_DATA0) & BIT(15)), readl(pon->mac + PDC_DATA0) & 7, (readl(pon->mac + PDC_DATA1) >> 4) & 511);
	}
	return length;
}
