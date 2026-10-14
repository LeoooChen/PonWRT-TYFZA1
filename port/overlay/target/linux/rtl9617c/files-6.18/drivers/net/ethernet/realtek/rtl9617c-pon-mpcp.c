// SPDX-License-Identifier: GPL-2.0-or-later
/* Native MPCP control, LLID access and events; GPL aal_mpcp.c reference. */
#include <linux/bitfield.h>
#include <linux/etherdevice.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/jiffies.h>
#include <linux/unaligned.h>
#include "rtl9617c-pon.h"

#define GLB_IRQ 0x0000
#define GLB_IE 0x0004
#define MAC_HIGH 0x0018
#define MAC_LOW 0x001c
#define LLID_TABLE 0x0064
#define LLID_DATA 0x0164
#define LLID_ACCESS 0x0168
#define LLID_RESULT 0x016c
#define LLID_MAPPING 0x0170
#define MPCP_IRQ 0x2000
#define MPCP_IE 0x2004
#define MPCP_INIT 0x2008
#define MPCP_START RTL_MPCP_START
#define REGISTER_FRAME 0x2010
#define REGISTER_STATUS 0x2014
#define MPCP_CFG0 0x2020
#define MPCP_CFG 0x2024
#define MPCP_1G 0x2028
#define MPCP_10G 0x202c
#define LASER_TIME 0x203c
#define TIMEOUT_THRESHOLD 0x204c
#define REGISTER_TIMEOUT 0x2050
#define SYNC_TIME 0x20d0
#define OFFLINE 0x20e0
#define MPCP_EVENT_MASK (BIT(0) | BIT(1) | BIT(2) | BIT(3) | BIT(6) | BIT(14))
#define OPT_EVENT_MASK (BIT(0) | BIT(1) | BIT(2) | BIT(8) | BIT(9))
#define PON_TOP_EVENTS (BIT(0) | BIT(3))

static u32 rtl_pon_llid_mask(struct rtl9617c_pon *pon)
{
	return GENMASK(pon->llid_count - 1, 0);
}

int rtl_pon_llid_update(struct rtl9617c_pon *pon, bool add, unsigned int index,
			 unsigned int mac, u16 llid, bool multicast)
{
	u32 status;
	int ret;

	writel(llid | (index << 16) | ((u32)multicast << 22) |
	       ((u32)add << 23) | (mac << 24), pon->mac + LLID_DATA);
	writel(BIT(0), pon->mac + LLID_ACCESS);
	ret = readl_poll_timeout(pon->mac + LLID_ACCESS, status, !(status & BIT(0)), 1, 100000);
	if (ret)
		return ret;
	status = readl(pon->mac + LLID_RESULT) & 7;
	if (add && (status == 6 || status == 7))
		return 0;
	if (!add && status == 5)
		return 0;
	if (status == 1)
		return -ENOSPC;
	if (status == 2)
		return -ENOENT;
	return -EIO;
}

static void rtl_mpcp_remove_mac(struct rtl9617c_pon *pon, unsigned int mac)
{
	unsigned int i;

	for (i = 0; i < 64; i++) {
		u32 entry = readl(pon->mac + LLID_TABLE + i * 4);
		if ((entry & BIT(23)) && !(entry & BIT(22)) &&
		    FIELD_GET(GENMASK(28, 24), entry) == mac)
			rtl_pon_llid_update(pon, false, FIELD_GET(GENMASK(21, 16), entry),
					    mac, entry & 0xffff, false);
	}
}

static void rtl_mpcp_registration_event(struct rtl9617c_pon *pon, u32 registered)
{
	char value[48];
	char *env[] = {"PON_EVENT=registration", value, NULL};

	snprintf(value, sizeof(value), "PON_REGISTERED=%08x", registered);
	pon->registration_events++;
	kobject_uevent_env(&pon->dev->kobj, KOBJ_CHANGE, env);
	dev_info(pon->dev, "MPCP registered LLIDs: %08x -> %08x\n", pon->registered, registered);
}

static unsigned int rtl_mpcp_read(void *context, unsigned int reg)
{
	struct rtl9617c_pon *pon = context;

	return readl(pon->mac + reg);
}

static void rtl_mpcp_write(void *context, unsigned int reg, unsigned int value)
{
	struct rtl9617c_pon *pon = context;

	writel(value, pon->mac + reg);
}

static void rtl_mpcp_refresh(struct rtl9617c_pon *pon)
{
	u32 flags = readl(pon->mac + MPCP_IRQ) & MPCP_EVENT_MASK;
	u32 registered;
	unsigned int i;
	const struct rtl_mpcp_io io = {pon, rtl_mpcp_read, rtl_mpcp_write};

	pon->last_mpcp_irq = flags;
	if (rtl_mpcp_backoff_event(&pon->backoff, &io, flags, jiffies,
				   msecs_to_jiffies(60000)) && pon->llid_count == 1)
		rtl_mpcp_remove_mac(pon, 0);
	if (flags & BIT(1)) {
		for (i = 0; i < 64; i++) {
			u32 frame = readl(pon->mac + REGISTER_FRAME);
			if (!(frame & BIT(25)))
				break;
			pon->register_frames++;
		}
	}
	if (flags)
		writel(flags, pon->mac + MPCP_IRQ);
	registered = readl(pon->mac + REGISTER_STATUS) & rtl_pon_llid_mask(pon);
	if (pon->registered & ~registered) {
		u32 lost = pon->registered & ~registered;
		for (i = 0; i < pon->llid_count; i++)
			if (lost & BIT(i))
				rtl_mpcp_remove_mac(pon, i);
	}
	if (registered != pon->registered)
		rtl_mpcp_registration_event(pon, registered);
	pon->registered = registered;
	pon->waiting = rtl_pon_llid_mask(pon) & ~registered;
	rtl_pon_pdc_refresh(pon);
	rtl_pon_oam_carrier(pon);
}

static void rtl_mpcp_work(struct work_struct *work)
{
	struct rtl9617c_pon *pon = container_of(to_delayed_work(work), struct rtl9617c_pon, mpcp_work);
	u32 status;
	bool synced;
	unsigned int mac;

	mutex_lock(&pon->lock);
	if (!pon->mpcp_enabled)
		goto unlock;
	rtl_mpcp_refresh(pon);
	status = readl(pon->mac + 0x10);
	synced = !(status & BIT(0)) &&
		 !!(status & (pon->mode == RTL_PON_EPON_1G ? BIT(2) : BIT(1)));
	if (pon->mpcp_enabled && synced && !rtl_mpcp_backoff_active(&pon->backoff, jiffies)) {
		for (mac = 0; mac < pon->llid_count; mac++) {
			if (!(pon->waiting & BIT(mac)))
				continue;
			rtl_pon_update(pon->mac + MPCP_INIT, GENMASK(13, 8) | GENMASK(4, 0),
					   (mac << 8) | mac);
			rtl_pon_update(pon->mac + MPCP_START, BIT(0), BIT(0));
			pon->registration_attempts++;
			break;
		}
	}
	if (pon->mpcp_enabled)
		schedule_delayed_work(&pon->mpcp_work, HZ);
unlock:
	mutex_unlock(&pon->lock);
}

static irqreturn_t rtl_mpcp_irq_thread(int irq, void *data)
{
	struct rtl9617c_pon *pon = data;
	u32 pending = readl(pon->top_irq) & readl(pon->top_irq + 4);

	if (!(pending & PON_TOP_EVENTS))
		return IRQ_NONE;
	mutex_lock(&pon->lock);
	if (pending & BIT(3)) {
		u32 optical = readl(pon->optical_irq) & readl(pon->optical_irq + 4);

		pon->last_optical_irq = optical;
		pon->optical_irq_count++;
		writel(optical, pon->optical_irq);
		if (rtl_pon_mode_is_epon(pon->mode))
			rtl_pon_oam_carrier(pon);
	}
	if (pending & BIT(0)) {
		if (rtl_pon_mode_is_epon(pon->mode)) {
			pon->mpcp_irq_count++;
			rtl_mpcp_refresh(pon);
			writel(BIT(3), pon->mac + GLB_IRQ);
		} else if (pon->mode == RTL_PON_GPON) {
			rtl_pon_gpon_irq(pon);
		} else {
			rtl_pon_xgpon_irq(pon);
		}
	}
	writel(pending & PON_TOP_EVENTS, pon->top_irq);
	mutex_unlock(&pon->lock);
	return IRQ_HANDLED;
}

int rtl_pon_mpcp_init(struct rtl9617c_pon *pon, int irq)
{
	pon->mpcp_irq = irq;
	pon->llid_count = 1;
	INIT_DELAYED_WORK(&pon->mpcp_work, rtl_mpcp_work);
	/* This controller owns the PON bank. Remove inherited optical enables and
	 * acknowledge their leaf events before request_irq unmasks its parent. */
	writel(0, pon->top_irq + 4);
	writel(0, pon->optical_irq + 4);
	writel(readl(pon->optical_irq) & OPT_EVENT_MASK, pon->optical_irq);
	writel(PON_TOP_EVENTS, pon->top_irq);
	writel(OPT_EVENT_MASK, pon->optical_irq + 4);
	writel(BIT(3), pon->top_irq + 4);
	writel(0, pon->mac + MPCP_IE);
	return devm_request_threaded_irq(pon->dev, irq, NULL, rtl_mpcp_irq_thread,
					 IRQF_ONESHOT, "rtl9617c-mpcp", pon);
}

void rtl_pon_mpcp_stop_locked(struct rtl9617c_pon *pon)
{
 if(pon->fe_enabled)rtl_pon_fe_configure_locked(pon,false);
	lockdep_assert_held(&pon->control_lock);
	cancel_delayed_work_sync(&pon->mpcp_work);
	mutex_lock(&pon->lock);
	pon->mpcp_enabled = false;
	pon->backoff.active = 0;
	if (!rtl_pon_mode_is_epon(pon->mode)) {
		mutex_unlock(&pon->lock);
		return;
	}
	rtl_pon_update(pon->top_irq + 4, BIT(0), 0);
	writel(0, pon->mac + MPCP_IE);
	rtl_pon_update(pon->mac + GLB_IE, BIT(3), 0);
	rtl_pon_update(pon->mac + MPCP_CFG, BIT(31), 0);
	rtl_pon_update(pon->mac + MPCP_START, BIT(0), 0);
	writel(rtl_pon_llid_mask(pon), pon->mac + OFFLINE);
	pon->waiting = 0;
	pon->registered = readl(pon->mac + REGISTER_STATUS) & rtl_pon_llid_mask(pon);
	pon->mpcp_configured = false;
	rtl_pon_oam_carrier(pon);
	mutex_unlock(&pon->lock);
	/* Finish queued IRQ threads before changing modes or freeing the OAM netdev. */
	synchronize_irq(pon->mpcp_irq);
}

int rtl_pon_mpcp_configure_locked(struct rtl9617c_pon *pon)
{
	u32 sync;
	int ret = 0;

	lockdep_assert_held(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (!rtl_pon_mode_is_epon(pon->mode)) {
		ret = -EOPNOTSUPP;
		goto unlock;
	}
	if (!pon->mac_prepared || !is_valid_ether_addr(pon->onu_mac)) {
		ret = -EAGAIN;
		goto unlock;
	}
	/* MPCP identity is the LLID0 MAC stored in this board's original MIB. */
	rtl_pon_update(pon->mac + MAC_HIGH, GENMASK(15, 0), get_unaligned_be16(pon->onu_mac));
	writel(get_unaligned_be32(pon->onu_mac + 2), pon->mac + MAC_LOW);
	rtl_pon_update(pon->mac + LLID_MAPPING,
		GENMASK(5, 0) | BIT(7) | GENMASK(21, 16), 63 | BIT(7) | (63 << 16));
	sync = pon->llid_count == 1 ? 0xf800 : pon->llid_count <= 2 ? 0xf000 :
	       pon->llid_count <= 4 ? 0xe000 : pon->llid_count <= 8 ? 0xc000 :
	       pon->llid_count <= 16 ? 0x8000 : 0;
	rtl_pon_update(pon->mac + SYNC_TIME, GENMASK(15, 11), sync);
	rtl_pon_update(pon->mac + MPCP_CFG0,
		GENMASK(3, 0) | BIT(5) | BIT(7) | GENMASK(10, 8) | GENMASK(18, 13) |
		BIT(23) | BIT(29) | BIT(30),
		4 | BIT(7) | (8 << 13) | (pon->llid_count > 1 ? BIT(29) : 0));
	rtl_pon_update(pon->mac + MPCP_1G, GENMASK(15, 0), (0xf << 8) | 0x2a);
	rtl_pon_update(pon->mac + MPCP_10G, GENMASK(15, 8), 0xc << 8);
	rtl_pon_update(pon->mac + TIMEOUT_THRESHOLD, GENMASK(13, 0) | GENMASK(29, 16),
		5000 | (500 << 16));
	rtl_pon_update(pon->mac + MPCP_CFG, BIT(1) | GENMASK(18, 16) | BIT(31),
		GENMASK(18, 16) | BIT(31));
	rtl_pon_update(pon->mac + REGISTER_TIMEOUT, GENMASK(19, 0) | BIT(31), 0xffff | BIT(31));
	rtl_pon_update(pon->mac + LASER_TIME, GENMASK(7, 0) | GENMASK(23, 16), 32 | (32 << 16));
	writel(0, pon->mac + OFFLINE);
	pon->backoff.active = 0;
	pon->registered = readl(pon->mac + REGISTER_STATUS) & rtl_pon_llid_mask(pon);
	pon->waiting = rtl_pon_llid_mask(pon) & ~pon->registered;
	pon->mpcp_configured = true;
	pon->mpcp_enabled = true;
	writel(MPCP_EVENT_MASK, pon->mac + MPCP_IRQ);
	writel(MPCP_EVENT_MASK, pon->mac + MPCP_IE);
	rtl_pon_update(pon->mac + GLB_IE, BIT(3), BIT(3));
	rtl_pon_update(pon->top_irq + 4, PON_TOP_EVENTS, PON_TOP_EVENTS);
	schedule_delayed_work(&pon->mpcp_work, HZ);
	dev_info(pon->dev, "native MPCP configured: ONU %pM, %u LLIDs\n", pon->onu_mac, pon->llid_count);
unlock:
	mutex_unlock(&pon->lock);
	return ret;
}

ssize_t rtl_pon_mpcp_state(struct rtl9617c_pon *pon, char *buf)
{
	return sysfs_emit(buf,
		"configured=%u enabled=%u mac=%pM llids=%u registered=%08x waiting=%08x attempts=%u events=%u irq_count=%u frames=%u cfg0=%08x cfg=%08x sync=%08x timers=%08x register_timeout=%08x laser_time=%08x mapping=%08x init=%08x start=%08x register_flag=%u nacks=%u silence_scope=shared-engine silence_ms=%u\n",
		pon->mpcp_configured, pon->mpcp_enabled, pon->onu_mac, pon->llid_count,
		pon->registered, pon->waiting, pon->registration_attempts, pon->registration_events,
		pon->mpcp_irq_count, pon->register_frames, readl(pon->mac + MPCP_CFG0),
		readl(pon->mac + MPCP_CFG), readl(pon->mac + SYNC_TIME),
		readl(pon->mac + TIMEOUT_THRESHOLD), readl(pon->mac + REGISTER_TIMEOUT),
		readl(pon->mac + LASER_TIME), readl(pon->mac + LLID_MAPPING),
		readl(pon->mac + MPCP_INIT), readl(pon->mac + MPCP_START),
		pon->backoff.register_flag, pon->backoff.nacks,
		pon->backoff.active && time_before(jiffies, pon->backoff.until) ?
		jiffies_to_msecs(pon->backoff.until - jiffies) : 0);
}

ssize_t rtl_pon_llid_table(struct rtl9617c_pon *pon, char *buf)
{
	ssize_t length = 0;
	unsigned int i;
	for (i = 0; i < 64; i++) {
		u32 value = readl(pon->mac + LLID_TABLE + i * 4);
		if (!(value & BIT(23)))
			continue;
		length += scnprintf(buf + length, PAGE_SIZE - length,
			"entry=%u index=%u mac=%u llid=%04x multicast=%u\n", i,
			(unsigned int)FIELD_GET(GENMASK(21, 16), value),
			(unsigned int)FIELD_GET(GENMASK(28, 24), value), value & 0xffff, !!(value & BIT(22)));
	}
	return length;
}
