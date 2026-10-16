// SPDX-License-Identifier: GPL-2.0-or-later
/* XG-PON/XGS-PON preparation from this board's original XGTC initializers. */
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/jiffies.h>
#include <linux/random.h>
#include <linux/slab.h>
#include <linux/unaligned.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-xgpon-hw.h"
#include "rtl9617c-xgpon-reset.h"

static unsigned int rtl_xgpon_read(void *context, unsigned int reg)
{
	return readl(((struct rtl9617c_pon *)context)->mac + reg);
}

static void rtl_xgpon_write(void *context, unsigned int reg, unsigned int value)
{
	writel(value, ((struct rtl9617c_pon *)context)->mac + reg);
}

static int rtl_xgpon_indirect(void *context, unsigned int reg, unsigned int value)
{
	struct rtl9617c_pon *pon = context;
	u32 status;
	int ret = readl_poll_timeout(pon->mac + reg, status, !(status & BIT(31)), 1, 100000);

	if (ret) return ret;
	writel(value, pon->mac + reg);
	return readl_poll_timeout(pon->mac + reg, status, !(status & BIT(31)), 1, 100000);
}

ssize_t rtl_pon_xgpon_identity(struct rtl9617c_pon *pon, const char *buf, size_t count)
{
	ssize_t ret = count;

	if (count != 44)
		return -EMSGSIZE;
	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (rtl_pon_mode_is_epon(pon->mode) || pon->mode == RTL_PON_GPON)
		ret = -EOPNOTSUPP;
	else {
		memcpy(pon->xgpon_serial, buf, 8);
		memcpy(pon->xgpon_registration, buf + 8, 36);
		pon->xgpon_identity_configured = true;
	}
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	return ret;
}

int rtl_pon_xgpon_prepare_locked(struct rtl9617c_pon *pon)
{
	const struct rtl_dq_io io = {pon, rtl_xgpon_read, rtl_xgpon_write, rtl_xgpon_indirect};
	unsigned int index;
	bool symmetric = pon->mode == RTL_PON_XGSPON;
	int ret = 0;

	lockdep_assert_held(&pon->control_lock);
	if (pon->mode != RTL_PON_XGPON && pon->mode != RTL_PON_XGSPON)
		return -EOPNOTSUPP;
	ret = rtl_pon_stop_locked(pon);
	mutex_lock(&pon->lock);
	pon->mac_prepared = false;
	if (ret)
		goto unlock;
	if (!pon->serdes_configured || !pon->xgpon_identity_configured) {
		ret = -EAGAIN;
		goto unlock;
	}
	pon->puc_configured = false;
	pon->pdc_configured = false;
	pon->traffic_enabled = false;
	pon->xgpon_pending_valid = false;
	memset(pon->xgpon_restore_tcont, 0, sizeof(pon->xgpon_restore_tcont));
	/* Full initialization uses aal_xgpon_init timing, not the warm-reset path. */
	writel(0x00e, pon->control);
	writel(0x300, pon->gpon_control);
	msleep(100);
	writel(0x30e, pon->control);
	writel(0x307, pon->gpon_control);
	msleep(100);
	writel(symmetric ? 0x305 : 0x205, pon->gpon_control);
	msleep(100);
	rtl_xgpon_mac_parameters(&io, symmetric);
	rtl_xgpon_identity(&io, pon->xgpon_serial, pon->xgpon_registration);
	/* Start without mappings from any preceding ONU session. */
	for (index = 0; index < 32; index++) {
		ret = rtl_xgpon_tcont_set(&io, index, RTL_XGPON_ALLOC_ID_INVALID, false, false);
		if (ret)
			goto unlock;
	}
	for (index = 0; index < 256; index++) {
		rtl_xgpon_downstream_gem_set(&io, index, 0, false, false);
		ret = rtl_xgpon_upstream_gem_set(&io, index, 0, false, false, false);
		if (ret)
			goto unlock;
	}
	writel(0x0002ffff, pon->optical_control + 4);
	pon->mac_prepared = true;
	dev_info(pon->dev, "%s MAC identity, ACTP and data blocks prepared\n",
		rtl_pon_mode_name(pon->mode));
unlock:
	pon->mac_error = ret;
	mutex_unlock(&pon->lock);
	return ret;
}

void rtl_pon_xgpon_stop_locked(struct rtl9617c_pon *pon)
{
	const struct rtl_dq_io io = {pon, rtl_xgpon_read, rtl_xgpon_write, rtl_xgpon_indirect};

	lockdep_assert_held(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (pon->mode == RTL_PON_XGPON || pon->mode == RTL_PON_XGSPON) {
		rtl_pon_dying_gasp_stop(pon);
		pon->xgpon_recover = false;
		cancel_delayed_work(&pon->xgpon_recovery_work);
		cancel_delayed_work(&pon->xgpon_health_work);
		pon->xgpon_restore_pending = false;
		pon->xgpon_watchdog_armed = false;
		pon->xgpon_rogue_pending = false;
		rtl_pon_itu_paths_invalidate(pon, pon->mode);
		pon->xgpon_enabled = false;
		pon->itu_line_epoch++;
		pon->xgpon_omcc_ready = false;
		pon->xgpon_key_valid = 0;
		pon->xgpon_key_state = 0;
		pon->omci_master_key_valid = false;
		rtl_pon_update(pon->mac + 0x4124, 3, 0);
		rtl_pon_update(pon->mac + 0x4128, 3, 0);
		spin_lock_bh(&pon->omci_key_lock);
		pon->xgpon_key_ready = false;
		spin_unlock_bh(&pon->omci_key_lock);
		writel(0, pon->mac + 0x400c);
		writel(0, pon->mac + 0x4d20);
		writel(0, pon->mac + 0x4aa8);
		writel(0, pon->mac + 0x411c);
		rtl_xgpon_active(&io, false, pon->mode == RTL_PON_XGSPON);
		rtl_pon_update(pon->top_irq + 4, BIT(0), 0);
		gpiod_set_value_cansleep(pon->tx_disable, 1);
		if (pon->xgpon_recovery_phase) {
			rtl9617c_ni_pon_gate(pon->ni_dev, pon->xgpon_puc_saved.ni_enable);
			pon->xgpon_recovery_phase = 0;
		}
		if (pon->omci_device)
			netif_carrier_off(pon->omci_device);
	}
	mutex_unlock(&pon->lock);
	synchronize_irq(pon->mpcp_irq);
}

int rtl_pon_xgpon_activation_locked(struct rtl9617c_pon *pon, bool enable)
{
	const struct rtl_dq_io io = {pon, rtl_xgpon_read, rtl_xgpon_write, rtl_xgpon_indirect};
	int ret = 0;

	lockdep_assert_held(&pon->control_lock);
	if (!enable) {
		rtl_pon_xgpon_stop_locked(pon);
		return 0;
	}
	mutex_lock(&pon->lock);
	if (pon->mode != RTL_PON_XGPON && pon->mode != RTL_PON_XGSPON) {
		ret = -EOPNOTSUPP;
		goto unlock;
	}
	if (pon->xgpon_recover) {
		ret = -EBUSY;
		goto unlock;
	}
	if (!pon->mac_prepared || !pon->puc_configured || !pon->pdc_configured ||
	    !netif_running(pon->omci_device)) {
		ret = -EAGAIN;
		goto unlock;
	}
	pon->xgpon_enabled = true;
	pon->xgpon_omcc_ready = false;
	pon->xgpon_activation_error = 0;
	pon->xgpon_health_state = ~0U;
	pon->xgpon_health_at = jiffies + HZ;
	pon->xgpon_watchdog_armed = false;
	writel(readl(pon->mac + 0x4d1c), pon->mac + 0x4d1c);
	writel(readl(pon->mac + 0x4aa4), pon->mac + 0x4aa4);
	writel(readl(pon->mac + 0x4118), pon->mac + 0x4118);
	/* ACTP controls activation; capture PLOAMs to maintain OMCC and its key. */
	writel(0x0fffffff, pon->mac + 0x4d2c);
	writel(15, pon->mac + 0x4d20);
	writel(BIT(5) | BIT(6) | BIT(7) | BIT(30) | BIT(31), pon->mac + 0x4aa8);
	writel(BIT(5), pon->mac + 0x411c);
	writel(BIT(0) | BIT(2) | BIT(4), pon->mac + 0x400c);
	rtl_pon_update(pon->top_irq + 4, BIT(0), BIT(0));
	rtl_pon_update(pon->serdes_init, BIT(4), BIT(4));
	ret = rtl_pon_serdes_restart_locked(pon);
	pon->serdes_error = ret;
	if (ret) {
		pon->xgpon_enabled = false;
		pon->xgpon_activation_error = ret;
		rtl_pon_update(pon->serdes_init, BIT(4), 0);
		gpiod_set_value_cansleep(pon->tx_disable, 1);
		netif_carrier_off(pon->omci_device);
		goto unlock;
	}
	pon->xgpon_last_state = readl(pon->mac + 0x4d30) & 7;
	rtl_xgpon_active(&io, true, pon->mode == RTL_PON_XGSPON);
	msleep(1);
	gpiod_set_value_cansleep(pon->tx_disable, 0);
	mod_delayed_work(system_wq, &pon->xgpon_health_work, msecs_to_jiffies(100));
unlock:
	mutex_unlock(&pon->lock);
	return ret;
}

void rtl_pon_xgpon_optical_irq(struct rtl9617c_pon *pon, u32 events)
{
	u32 now, elapsed;

	lockdep_assert_held(&pon->lock);
	if (!pon->xgpon_enabled || (events & BIT(0)) || !(events & (BIT(8) | BIT(9))))
		return;
	now = jiffies_to_msecs(jiffies);
	elapsed = now - pon->xgpon_rogue_last_ms;
	pon->xgpon_rogue_last_ms = now;
	if (elapsed < 1000) {
		pon->xgpon_rogue_count[events & BIT(8) ? 1 : 0]++;
		gpiod_set_value_cansleep(pon->tx_disable, 1);
		pon->xgpon_rogue_pending = true;
		dev_err(pon->dev, "XGTC optical rogue events=%08x interval_ms=%u; forcing O7\n", events, elapsed);
		mod_delayed_work(system_wq, &pon->xgpon_health_work, 0);
	}
	/* Original optical worker rearms detection after either interval outcome. */
	rtl_pon_update(pon->optical_control, BIT(1), 0);
	rtl_pon_update(pon->optical_control, BIT(1), BIT(1));
}

static int rtl_xgpon_mac_restart_locked(struct rtl9617c_pon *pon, bool phy)
{
	const struct rtl_dq_io io = {pon, rtl_xgpon_read, rtl_xgpon_write, rtl_xgpon_indirect};
	struct rtl_xgpon_mac_saved *saved;
	const char *stage = "snapshot";
	u32 control, gpon;
	int ret;

	lockdep_assert_held(&pon->control_lock);
	lockdep_assert_held(&pon->lock);
	saved = NULL;
	ret = rtl_pon_itu_paths_invalidate(pon, pon->mode);
	if (ret)
		goto free;
	saved = kvmalloc(sizeof(*saved), GFP_KERNEL);
	if (!saved) {
		ret = -ENOMEM;
		goto free;
	}
	pon->xgpon_enabled = false;
	rtl_pon_dying_gasp_stop(pon);
	pon->itu_line_epoch++;
	pon->xgpon_omcc_ready = false;
	pon->omci_master_key_valid = false;
	pon->xgpon_key_valid = 0;
	pon->xgpon_key_state = 0;
	spin_lock_bh(&pon->omci_key_lock);
	pon->xgpon_key_ready = false;
	spin_unlock_bh(&pon->omci_key_lock);
	netif_carrier_off(pon->omci_device);
	saved->puc.ni_enable = rtl9617c_ni_pon_gate(pon->ni_dev, 0);
	saved->puc.mac = readl(pon->mac + 0x4004);
	saved->puc.burst = readl(pon->serdes_init) & BIT(4);
	saved->puc.laser = readl(pon->mac + 0x4600) & BIT(2);
	saved->puc.to1 = readl(pon->mac + 0x4d0c);
	saved->puc.to2 = readl(pon->mac + 0x4d10);
	if (pon->xgpon_restore_pending) {
		for (unsigned int i = 0; i < 8; i++)
			writel(pon->xgpon_puc_saved.valid[i], pon->mac + 0x81bc - i * 4);
		pon->xgpon_restore_pending = false;
	}
	if (!(readl(pon->mac + 0xa064) & BIT(15)))
		rtl_pon_update(pon->serdes_init, BIT(4), 0);
	rtl_xgpon_active(&io, false, pon->mode == RTL_PON_XGSPON);
	udelay(500);
	ret = rtl_xgpon_mac_save(&io, saved);
	if (ret)
		goto ungate;
	stage = "reset";
	writel(0, pon->mac + 0x400c);
	rtl_pon_update(pon->top_irq + 4, BIT(0), 0);
	control = readl(pon->control) & ~0x300U;
	gpon = readl(pon->gpon_control) & ~7U;
	writel(gpon, pon->gpon_control);
	writel(control, pon->control);
	msleep(1);
	if (phy) {
		ret = rtl_pon_serdes_restart_locked(pon);
		pon->serdes_error = ret;
		if (ret)
			goto ungate;
	}
	writel(gpon | 5, pon->gpon_control);
	writel(control | 0x300, pon->control);
	msleep(1);
	rtl_xgpon_mac_parameters(&io, pon->mode == RTL_PON_XGSPON);
	rtl_xgpon_identity(&io, pon->xgpon_serial, pon->xgpon_registration);
	pon->mac_prepared = true;
	pon->puc_configured = false;
	pon->pdc_configured = false;
	stage = "datapath initialization";
	mutex_unlock(&pon->lock);
	ret = rtl_pon_pdc_configure_locked(pon);
	if (!ret)
		ret = rtl_pon_puc_configure_locked(pon);
	mutex_lock(&pon->lock);
	if (ret)
		goto ungate;
	stage = "configuration restore";
	ret = rtl_xgpon_mac_restore(&io, saved);
	if (ret)
		goto ungate;
	memcpy(pon->xgpon_puc_saved.valid, saved->puc.valid, sizeof(saved->puc.valid));
	pon->xgpon_restore_pending = true;
	pon->xgpon_restore_at = 0;
	writel(0x1007f, pon->mac + 0x402c);
	rtl_xgpon_random_load(&io, get_random_u32());
	writel(0x0fffffff, pon->mac + 0x4d28);
	writel(0x0fffffff, pon->mac + 0x4d2c);
	writel(readl(pon->mac + 0x4d1c), pon->mac + 0x4d1c);
	writel(readl(pon->mac + 0x4aa4), pon->mac + 0x4aa4);
	writel(readl(pon->mac + 0x4118), pon->mac + 0x4118);
	writel(15, pon->mac + 0x4d20);
	writel(BIT(5) | BIT(6) | BIT(7) | BIT(30) | BIT(31), pon->mac + 0x4aa8);
	writel(BIT(5), pon->mac + 0x411c);
	writel(BIT(0) | BIT(2) | BIT(4), pon->mac + 0x400c);
	rtl_pon_update(pon->top_irq + 4, BIT(0), BIT(0));
	rtl_pon_update(pon->mac + 0x4600, BIT(2), saved->puc.laser);
	writel(saved->puc.to1, pon->mac + 0x4d0c);
	writel(saved->puc.to2, pon->mac + 0x4d10);
	writel(saved->puc.mac, pon->mac + 0x4004);
	if (saved->puc.burst)
		udelay(500);
	rtl_pon_update(pon->serdes_init, BIT(4), saved->puc.burst);
	pon->xgpon_enabled = true;
	pon->xgpon_last_state = readl(pon->mac + 0x4d30) & 7;
	pon->xgpon_health_state = ~0U;
	pon->xgpon_pending_valid = false;
	pon->xgpon_mac_resets++;
ungate:
	rtl9617c_ni_pon_gate(pon->ni_dev, saved->puc.ni_enable);
free:
	if (ret)
		dev_err(pon->dev, "XGTC MAC recovery failed at %s: %d\n", stage, ret);
	kvfree(saved);
	return ret;
}

/* Original one-second O1/O23/O5 checks; a reset reason is retained for diagnosis. */
static unsigned int rtl_xgpon_health_check(struct rtl9617c_pon *pon, u32 state)
{
	u32 grants, sn, fec, delta, optical;

	if (pon->xgpon_health_state != state) {
		pon->xgpon_health_state = state;
		pon->xgpon_health_grants = 0;
		pon->xgpon_health_matches = 0;
		pon->xgpon_health_no_grants = 0;
		return 0;
	}
	if (!state) {
		optical = readl(pon->optical_status);
		if (optical & (BIT(3) | BIT(2))) {
			pon->xgpon_health_matches = 0;
			return 0;
		}
		fec = readl(pon->mac + 0x4d98);
		pon->xgpon_health_fec += fec;
		if (fec > 0x2000 &&
		    (readl(pon->mac + 0x452c) & 7) != 3) {
			if (++pon->xgpon_health_matches >= 3)
				return 1;
		} else {
			pon->xgpon_health_matches = 0;
		}
	} else if (state == 1) {
		grants = readl(pon->mac + 0x4860);
		sn = readl(pon->mac + 0x4c50);
		pon->xgpon_health_sn += sn;
		if (grants == pon->xgpon_health_grants) {
			if (++pon->xgpon_health_no_grants >= 20)
				return 2;
		} else {
			pon->xgpon_health_no_grants = 0;
			if (!sn && pon->xgpon_health_grants)
				pon->xgpon_health_matches++;
			else
				pon->xgpon_health_matches = 0;
			pon->xgpon_health_grants = grants;
		}
		if (pon->xgpon_health_matches >= 2)
			return 3;
	} else if (state == 3) {
		grants = readl(pon->mac + 0x4860);
		delta = grants > pon->xgpon_health_grants ? grants - pon->xgpon_health_grants :
			pon->xgpon_health_grants - grants;
		pon->xgpon_health_grants = grants;
		if (delta > 32)
			pon->xgpon_health_matches = 0;
		else if (++pon->xgpon_health_matches >= 5)
			return 4;
	} else {
		pon->xgpon_health_matches = 0;
		pon->xgpon_health_no_grants = 0;
	}
	return 0;
}

void rtl_pon_xgpon_health(struct work_struct *work)
{
	struct rtl9617c_pon *pon = container_of(to_delayed_work(work), struct rtl9617c_pon, xgpon_health_work);
	const struct rtl_dq_io io = {pon, rtl_xgpon_read, rtl_xgpon_write, rtl_xgpon_indirect};
	u32 state, ready, reason = 0;
	int ret;

	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (!pon->xgpon_enabled)
		goto unlock;
	if (pon->xgpon_rogue_pending) {
		pon->xgpon_rogue_pending = false;
		rtl_pon_dying_gasp_stop(pon);
		netif_carrier_off(pon->omci_device);
		msleep(1);
		rtl_pon_update(pon->serdes_init, BIT(4), BIT(4));
		ret = rtl_pon_serdes_restart_locked(pon);
		pon->serdes_error = ret;
		if (ret)
			goto stop;
		rtl_xgpon_active(&io, true, pon->mode == RTL_PON_XGSPON);
		rtl_pon_update(pon->mac + 0x4d00, BIT(0), BIT(0));
		rtl_pon_update(pon->mac + 0x4d30, 7, 5);
		rtl_pon_update(pon->mac + 0x4d00, BIT(0), 0);
		rtl_pon_itu_carrier(pon);
	}
	if (pon->xgpon_recover)
		goto reschedule;
	state = readl(pon->mac + 0x4d30) & 7;
	if (pon->xgpon_restore_pending) {
		if (state < 3) {
			pon->xgpon_restore_at = 0;
		} else if (!pon->xgpon_restore_at && state == 3) {
			for (unsigned int i = 1; i < 32; i++) {
				u32 word = pon->xgpon_restore_tcont[i];

				if (!(word & BIT(24)))
					continue;
				ret = rtl_xgpon_tcont_set(&io, i, word & 0x3fff, false, true);
				if (ret) {
					dev_err(pon->dev, "XGTC T-CONT restore failed index=%u: %d\n", i, ret);
					goto stop;
				}
			}
			pon->xgpon_restore_at = jiffies + msecs_to_jiffies(3000);
		} else if (pon->xgpon_restore_at && time_after_eq(jiffies, pon->xgpon_restore_at)) {
			for (unsigned int i = 0; i < 8; i++)
				writel(pon->xgpon_puc_saved.valid[i], pon->mac + 0x81bc - i * 4);
			pon->xgpon_restore_pending = false;
			rtl_pon_itu_carrier(pon);
		}
	}
	if (pon->xgpon_watchdog_armed) {
		for (unsigned int i = 0; i < 20; i++) {
			ready = readl(pon->mac + 0x4afc);
			if (!(ready & BIT(31)))
				break;
		}
		if (ready & BIT(31))
			reason = 5;
	}
	if (!reason && time_after_eq(jiffies, pon->xgpon_health_at)) {
		pon->xgpon_health_at = jiffies + HZ;
		reason = rtl_xgpon_health_check(pon, state);
	}
	if (reason) {
		pon->xgpon_reset_reason = reason;
		dev_err(pon->dev, "XGTC hardware stalled reason=%u ACTP=%08x grants=%08x PLOAM_TX=%08x\n",
			reason, readl(pon->mac + 0x4d30), readl(pon->mac + 0x4860), readl(pon->mac + 0x4afc));
		ret = rtl_xgpon_mac_restart_locked(pon, reason != 5);
		pon->xgpon_activation_error = ret;
		if (ret)
			goto stop;
	}
reschedule:
	schedule_delayed_work(&pon->xgpon_health_work, msecs_to_jiffies(100));
	goto unlock;
stop:
	pon->xgpon_activation_error = ret;
	pon->xgpon_enabled = false;
	rtl_pon_dying_gasp_stop(pon);
	pon->mac_prepared = false;
	rtl_xgpon_active(&io, false, pon->mode == RTL_PON_XGSPON);
	rtl_pon_update(pon->serdes_init, BIT(4), 0);
	gpiod_set_value_cansleep(pon->tx_disable, 1);
	netif_carrier_off(pon->omci_device);
	rtl_pon_itu_carrier(pon);
unlock:
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
}

void rtl_pon_xgpon_irq(struct rtl9617c_pon *pon)
{
	const struct rtl_dq_io io = {pon, rtl_xgpon_read, rtl_xgpon_write, rtl_xgpon_indirect};
	u32 status[2], global, act, plmp, dsfp, state, key_control, transitions;
	u8 order[2], packets[2][RTL_XGPON_PLOAM_BYTES];
	unsigned int count, i;
	bool was_active;
	int ret = 0;

	lockdep_assert_held(&pon->lock);
	global = readl(pon->mac + 0x4008);
	act = readl(pon->mac + 0x4d1c) & readl(pon->mac + 0x4d20);
	plmp = readl(pon->mac + 0x4aa4) & readl(pon->mac + 0x4aa8);
	dsfp = readl(pon->mac + 0x4118) & readl(pon->mac + 0x411c);
	key_control = readl(pon->mac + 0x4ab8);
	transitions = readl(pon->mac + 0x4d28) & readl(pon->mac + 0x4d2c);
	state = readl(pon->mac + 0x4d30) & 7;
	writel(act, pon->mac + 0x4d1c);
	writel(plmp, pon->mac + 0x4aa4);
	writel(dsfp, pon->mac + 0x4118);
	writel(transitions, pon->mac + 0x4d28);
	pon->xgpon_irq_count++;
	pon->xgpon_last_irq = global;
	if (!pon->xgpon_enabled)
		return;
	/* Original ACTP dispatch gates the laser in O7 and releases it in O23. */
	if (state == 5)
		gpiod_set_value_cansleep(pon->tx_disable, 1);
	else if (state == 1 && !pon->xgpon_rogue_pending)
		gpiod_set_value_cansleep(pon->tx_disable, 0);
	was_active = pon->xgpon_last_state == 3 || pon->xgpon_last_state == 4;
	if (transitions)
		pon->xgpon_last_transitions = transitions;
	if (transitions & BIT(11))
		pon->xgpon_deactivations++;
	if ((was_active && state != 3 && state != 4) ||
	    (transitions & (BIT(1) | BIT(10) | BIT(11)))) {
		rtl_pon_dying_gasp_stop(pon);
		pon->itu_line_epoch++;
		pon->xgpon_omcc_ready = false;
		if (pon->itu_paths_ready)
			rtl_pon_itu_paths_invalidate(pon, pon->mode);
		pon->omci_master_key_valid = false;
		pon->xgpon_key_valid = 0;
		pon->xgpon_key_state = 0;
		spin_lock_bh(&pon->omci_key_lock);
		pon->xgpon_key_ready = false;
		spin_unlock_bh(&pon->omci_key_lock);
		rtl_pon_update(pon->mac + 0x4e00, BIT(6), BIT(6));
		rtl_pon_update(pon->mac + 0x4124, 3, 0);
		rtl_pon_update(pon->mac + 0x4128, 3, 0);
	}
	status[0] = readl(pon->mac + 0x4b28);
	status[1] = readl(pon->mac + 0x4b2c);
	count = rtl_xgpon_ploam_order(status[0], status[1], order);
	/* Copy both old buffers before processing may permit a hardware refill. */
	for (i = 0; i < count; i++)
		rtl_xgpon_ploam_receive(&io, order[i], packets[i]);
	for (i = 0; i < count; i++) {
		const u8 *packet = packets[i];

		if (status[order[i]] & BIT(15)) {
			pon->ploam_mic_errors++;
			continue;
		}
		pon->ploam_rx++;
		/* Original burst-profile processing derives OMCI IK after PON-TAG changes. */
		if (packet[2] == 1)
			ret = rtl_pon_xgpon_omci_key(pon, packet + 25);
		if (packet[2] == 3 && !memcmp(packet + 6, pon->xgpon_serial, 8)) {
			unsigned int index, onu_id = get_unaligned_be16(packet + 4);

			pon->xgpon_omcc_ready = false;
			/* Stock assigns unencrypted OMCC XGEMs1..7 to the actual ONU-ID. */
			for (index = 1; index < 8; index++) {
				ret = rtl_xgpon_upstream_gem_set(&io, index, onu_id, true, false, false);
				if (ret) break;
				rtl_xgpon_downstream_gem_set(&io, index, onu_id, true, false);
			}
			if (!ret) {
				pon->xgpon_omcc_id = onu_id;
				pon->xgpon_omcc_ready = true;
			}
		}
		if (ret)
			pon->xgpon_activation_error = ret;
	}
	if ((transitions & BIT(12)) && pon->xgpon_key_ready) {
		ret = rtl_pon_xgpon_ack_update(pon);
		if (ret) pon->xgpon_activation_error = ret;
	}
	if (plmp & BIT(7))
		pon->xgpon_watchdog_armed = true;
	if (plmp & (BIT(5) | BIT(6) | BIT(7)))
		rtl_pon_xgpon_key_irq(pon, plmp, key_control);
	if (dsfp & BIT(5)) {
		ret = rtl_xgpon_omcc_aes_toggle(&io);
		if (ret) {
			pon->xgpon_activation_error = ret;
			pon->xgpon_omcc_ready = false;
			rtl_pon_dying_gasp_stop(pon);
			dev_err(pon->dev, "XGTC OMCC AES mode update failed: %d\n", ret);
		}
	}
	if (pon->xgpon_omcc_ready && state == 3 && pon->xgpon_key_ready)
		netif_carrier_on(pon->omci_device);
	else
		netif_carrier_off(pon->omci_device);
	if (pon->xgpon_omcc_ready && state == 3 && pon->xgpon_key_ready)
		rtl_pon_dying_gasp_prepare(pon);
	else if (state != 3 && state != 4)
		rtl_pon_dying_gasp_stop(pon);
	/* O6 retains business state together with the OMCC and session keys. */
	if (pon->itu_paths_ready && (!pon->xgpon_omcc_ready || !pon->xgpon_key_ready ||
	    (state != 3 && state != 4)))
		rtl_pon_itu_paths_invalidate(pon, pon->mode);
	rtl_pon_itu_carrier(pon);
	if (was_active && state != 3 && state != 4) {
		/* Outside O6 grace, drain T-CONT/PUC without rebuilding SerDes
		 * or replacing the configured FE policy. */
		netif_carrier_off(pon->omci_device);
		pon->xgpon_recover = true;
		if (transitions & BIT(23)) rtl_xgtc_restart_sync(&io);
		schedule_delayed_work(&pon->xgpon_recovery_work, 0);
	}
	if (state != pon->xgpon_last_state || (transitions & BIT(11))) {
		dev_info(pon->dev, "%s ACTP %u -> %u ONU-ID=%u OMCC=%u error=%d events=%08x ploam_irq=%08x\n",
			rtl_pon_mode_name(pon->mode), pon->xgpon_last_state, state,
			pon->xgpon_omcc_id, pon->xgpon_omcc_ready, pon->xgpon_activation_error,
			transitions, plmp);
		pon->xgpon_last_state = state;
	}
}

void rtl_pon_xgpon_recovery(struct work_struct *work)
{
	struct rtl9617c_pon *pon = container_of(to_delayed_work(work), struct rtl9617c_pon, xgpon_recovery_work);
	const struct rtl_dq_io io = {pon, rtl_xgpon_read, rtl_xgpon_write, rtl_xgpon_indirect};
	struct rtl_xgtc_puc_saved *saved = &pon->xgpon_puc_saved;
	u32 status;
	int ret = 0;

	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (!pon->xgpon_recover) goto unlock;
	if (!pon->xgpon_recovery_phase) {
		status = readl(pon->mac + 0x4d30) & 7;
		if (status == 3 || status == 4) {
			pon->xgpon_recover = false;
			goto unlock;
		}
		if (pon->xgpon_restore_pending) {
			for (unsigned int i = 0; i < 8; i++)
				writel(pon->xgpon_puc_saved.valid[i], pon->mac + 0x81bc - i * 4);
			pon->xgpon_restore_pending = false;
		}
		saved->ni_enable = rtl9617c_ni_pon_gate(pon->ni_dev, 0);
		saved->mac = readl(pon->mac + 0x4004);
		saved->burst = readl(pon->serdes_init) & BIT(4);
		saved->laser = readl(pon->mac + 0x4600) & BIT(2);
		saved->to1 = readl(pon->mac + 0x4d0c);
		saved->to2 = readl(pon->mac + 0x4d10);
		if (!(readl(pon->mac + 0xa064) & BIT(15)))
			rtl_pon_update(pon->serdes_init, BIT(4), 0);
		rtl_pon_update(pon->mac + 0x4004, 3, 0);
		udelay(500);
		pon->xgpon_recovery_phase = 1;
	}
	if (pon->xgpon_recovery_phase == 1) {
		ret = rtl_pon_itu_paths_invalidate(pon, pon->mode);
		if (ret) goto retry;
		ret = rtl_xgtc_puc_save(&io, saved);
		if (ret) goto retry;
		pon->xgpon_recovery_phase = 2;
	}
	if (pon->xgpon_recovery_phase == 2) {
		for (unsigned int index = 0; index < 32; index++) {
			ret = readl_poll_timeout(pon->mac + 0x4004, status, !(status & BIT(31)), 0, 100000);
			if (ret) goto retry;
			rtl_pon_update(pon->mac + 0x4004, GENMASK(28, 24) | GENMASK(11, 8) | BIT(31),
				(index << 24) | BIT(31));
			ret = readl_poll_timeout(pon->mac + 0x4004, status, !(status & BIT(31)), 0, 100000);
			if (ret) goto retry;
		}
		rtl_pon_update(pon->control, BIT(8), 0);
		msleep(5);
		rtl_pon_update(pon->control, BIT(8), BIT(8));
		msleep(5);
		mutex_unlock(&pon->lock);
		ret = rtl_pon_puc_configure_locked(pon);
		mutex_lock(&pon->lock);
		if (ret) goto retry;
		pon->xgpon_recovery_phase = 3;
	}
	ret = rtl_xgtc_puc_restore(&io, saved);
	if (ret) goto retry;
	rtl_pon_update(pon->mac + 0x4600, BIT(2), saved->laser);
	writel(saved->to1, pon->mac + 0x4d0c);
	writel(saved->to2, pon->mac + 0x4d10);
	writel(saved->mac, pon->mac + 0x4004);
	if (saved->burst) udelay(500);
	rtl_pon_update(pon->serdes_init, BIT(4), saved->burst);
	rtl9617c_ni_pon_gate(pon->ni_dev, saved->ni_enable);
	pon->xgpon_recovery_phase = 0;
retry:
	pon->xgpon_recover = !!ret;
	pon->xgpon_activation_error = ret;
	if (ret) schedule_delayed_work(&pon->xgpon_recovery_work, HZ);
unlock:
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
}
