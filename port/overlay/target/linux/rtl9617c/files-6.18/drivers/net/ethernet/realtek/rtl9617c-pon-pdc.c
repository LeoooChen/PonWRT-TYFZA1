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

int rtl_pon_epon_paths_invalidate(struct rtl9617c_pon *pon)
{
 int ret;
 lockdep_assert_held(&pon->lock);
 if(!pon->epon_paths_ready && !pon->epon_paths_error)return 0;
 smp_store_release(&pon->epon_paths_ready,false);
 pon->epon_path_epoch++;
 ret=rtl9617c_ni_epon_paths(pon->ni_dev,NULL);
 pon->epon_paths_error=ret;
 return ret;
}

int rtl_pon_epon_paths_refresh(struct rtl9617c_pon *pon)
{
 struct rtl_epon_paths paths={};
 u32 status,raw_registered;
 bool synced;
 int ret;
 lockdep_assert_held(&pon->lock);
 if(!rtl_pon_mode_is_epon(pon->mode) || !pon->traffic_enabled || !pon->mpcp_enabled ||
    !pon->puc_configured || !pon->pdc_configured || !pon->fe_enabled)
  return rtl_pon_epon_paths_invalidate(pon);
 status=readl(pon->mac+0x10);raw_registered=readl(pon->mac+0x2014);
 synced=!(status&BIT(0)) && (status&(pon->mode==RTL_PON_EPON_1G?BIT(2):BIT(1)));
 paths.count=pon->llid_count;paths.queues=paths.count>16?4:8;paths.mode=pon->mode;
 if(readl(pon->mac+0x804c)!=(paths.queues==4?1U:0U))
  return rtl_pon_epon_paths_invalidate(pon)?:-EUCLEAN;
 for(unsigned int i=0;i<paths.count;i++) {
  unsigned int q=i*paths.queues;
  paths.queue_mask[i]=(readl(pon->mac+0x81bc-q/32*4)>>(q%32))&((1U<<paths.queues)-1);
 }
 paths.active=pon->registered&(synced?raw_registered:pon->epon_holdover_active);
 paths.active&=paths.count==32?~0U:BIT(paths.count)-1;
 for(unsigned int i=0;i<paths.count && i<8;i++)
  if(!pon->epon_sla_length[i])paths.active&=~BIT(i);
 paths.registration_epoch=pon->epon_epoch;
 for(unsigned int i=0;i<64;i++) {
  paths.table[i]=readl(pon->mac+0x64+i*4);
  if(pon->pdc_source[i]!=rtl_pon_data_source(i,paths.table[i]))
   return rtl_pon_epon_paths_invalidate(pon)?:-EAGAIN;
 }
 paths.generation=pon->epon_paths.generation;
 if(pon->epon_paths_ready && !memcmp(&paths,&pon->epon_paths,sizeof(paths)))return 0;
 ret=rtl_pon_epon_paths_invalidate(pon);if(ret)return ret;
 paths.generation=++pon->epon_path_epoch;
 ret=rtl9617c_ni_epon_paths(pon->ni_dev,&paths);
 pon->epon_paths_error=ret;
 if(!ret){pon->epon_paths=paths;smp_store_release(&pon->epon_paths_ready,true);}
 return ret;
}

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
	if (!pon->pdc_configured || !rtl_pon_mode_is_epon(pon->mode))
		return 0;
	/* A previous failed withdrawal revoked admission but retained FE owners.
	 * Finish that retry before writing any newly observed PDC mapping. */
	if (pon->epon_paths_error) {
		ret=rtl_pon_epon_paths_invalidate(pon);if(ret)return ret;
	}
	/* LLID contents may change while retaining the same MAC index. Compare
	 * the whole table before changing PDC or admitting the next snapshot. */
	if (pon->epon_paths_ready)
		for (index=0;index<64;index++)
			if (pon->epon_paths.table[index]!=readl(pon->mac+0x64+index*4)) {
				ret=rtl_pon_epon_paths_invalidate(pon);if(ret)return ret;
				break;
			}
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

static int rtl_pdc_itu_configure(struct rtl9617c_pon *pon)
{
	unsigned int index;
	int ret;

	/* ITU slots0..7 are OMCC; all remaining selectors are internal GEM indices. */
	for (index = 0; index < 256; index++) {
		u32 word = (16 << 3) | (7 << 9) | BIT(15);
		u32 pol_id = index < 8 ? index + 128 : index - 8;

		if (index < 8)
			word |= BIT(31) | 6;
		writel(word, pon->mac + PDC_DATA0);
		writel(pol_id << 4, pon->mac + PDC_DATA1);
		ret = rtl_pdc_access(pon, index, true);
		if (ret)
			return ret;
	}
	rtl_pon_update(pon->mac + PDC_CTRL, GENMASK(25, 16) | GENMASK(2, 0),
		BIT(1) | (7 << 16) | (16 << 19) | BIT(25));
	rtl_pon_update(pon->mac + PDC_COUNTER, GENMASK(2, 0), 0);
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
	ret=rtl_pon_epon_paths_invalidate(pon);if(ret)goto unlock;
	pon->pdc_configured = false;
	if (!rtl_pon_mode_is_epon(pon->mode)) {
		ret = rtl_pdc_itu_configure(pon);
		pon->pdc_configured = !ret;
		goto unlock;
	}
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
 ret=rtl_pon_epon_paths_invalidate(pon);if(ret)goto unlock;
 if(enable && (!pon->pdc_configured || !pon->puc_configured ||
               (rtl_pon_mode_is_epon(pon->mode) && !pon->traffic_enabled))) {
  ret=-EAGAIN;
  goto unlock;
 }
 if(enable) {
  ret=rtl9617c_ni_fe_enable(pon->ni_dev);
  if(ret)goto unlock;
  pon->fe_enabled=true;
 }
 /* OAM/control maps retain CPU16 and their original header container. */
 if(pon->pdc_configured && rtl_pon_mode_is_epon(pon->mode))
  for(entry=0;entry<64;entry++)
   for(bank=0;bank<2;bank++) {
    writel(((enable?24:16)<<3)|(7<<9)|(enable?0:BIT(15)),pon->mac+PDC_DATA0);
    writel((pon->pdc_source[entry]-32)<<4,pon->mac+PDC_DATA1);
    ret=rtl_pdc_access(pon,entry|(bank<<7),true);
    if(ret)goto unlock;
   }
 if(pon->pdc_configured && !rtl_pon_mode_is_epon(pon->mode))
  for(entry=8;entry<256;entry++) {
   u32 data;
   /* ITU selectors0..7 retain OMCI CPU containers; data follows the GEM map. */
   ret=rtl_pdc_access(pon,entry,false);
   if(ret)goto unlock;
   data=readl(pon->mac+PDC_DATA0);
   writel((data&~((63U<<3)|BIT(15)))|((enable?24:16)<<3)|
    (enable?0:BIT(15)),pon->mac+PDC_DATA0);
   ret=rtl_pdc_access(pon,entry,true);
   if(ret)goto unlock;
  }
 if(!enable) {
  ret=rtl9617c_ni_fe_disable(pon->ni_dev);
  if(ret)goto unlock;
 }
 pon->fe_enabled=enable;
 if(enable && rtl_pon_mode_is_epon(pon->mode))ret=rtl_pon_epon_paths_refresh(pon);
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
			"index=%u data=%08x/%08x destination=%u source=%u bypass=%u cos=%u pol_id=%u\n",
			sample[i], readl(pon->mac + PDC_DATA0), readl(pon->mac + PDC_DATA1),
			(unsigned int)FIELD_GET(GENMASK(8, 3), readl(pon->mac + PDC_DATA0)),
			(unsigned int)FIELD_GET(GENMASK(14, 9), readl(pon->mac + PDC_DATA0)),
			!!(readl(pon->mac + PDC_DATA0) & BIT(15)), readl(pon->mac + PDC_DATA0) & 7, (readl(pon->mac + PDC_DATA1) >> 4) & 511);
	}
	return length;
}
