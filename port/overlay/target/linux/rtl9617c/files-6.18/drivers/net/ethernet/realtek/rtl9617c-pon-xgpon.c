// SPDX-License-Identifier: GPL-2.0-or-later
/* XG-PON/XGS-PON preparation from this board's original XGTC initializers. */
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/unaligned.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-xgpon-hw.h"

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
	writel(0x00e, pon->control);
	writel(0x300, pon->gpon_control);
	msleep(1);
	writel(0x30e, pon->control);
	writel(0x307, pon->gpon_control);
	msleep(1);
	writel(symmetric ? 0x305 : 0x205, pon->gpon_control);
	msleep(1);
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
		pon->xgpon_recover = false;
		cancel_delayed_work(&pon->xgpon_recovery_work);
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
	pon->xgpon_last_state = readl(pon->mac + 0x4d30) & 7;
	writel(readl(pon->mac + 0x4d1c), pon->mac + 0x4d1c);
	writel(readl(pon->mac + 0x4aa4), pon->mac + 0x4aa4);
	/* ACTP controls activation; capture PLOAMs to maintain OMCC and its key. */
	writel(15, pon->mac + 0x4d20);
	writel(BIT(5) | BIT(6) | BIT(7) | BIT(30) | BIT(31), pon->mac + 0x4aa8);
	writel(BIT(2) | BIT(4), pon->mac + 0x400c);
	rtl_pon_update(pon->top_irq + 4, BIT(0), BIT(0));
	rtl_pon_update(pon->serdes_init, BIT(4), BIT(4));
	gpiod_set_value_cansleep(pon->tx_disable, 0);
	rtl_xgpon_active(&io, true, pon->mode == RTL_PON_XGSPON);
unlock:
	mutex_unlock(&pon->lock);
	return ret;
}

void rtl_pon_xgpon_irq(struct rtl9617c_pon *pon)
{
	const struct rtl_dq_io io = {pon, rtl_xgpon_read, rtl_xgpon_write, rtl_xgpon_indirect};
	u32 status[2], global, act, plmp, state, key_control, transitions;
	u8 order[2], packets[2][RTL_XGPON_PLOAM_BYTES];
	unsigned int count, i;
	bool was_active;
	int ret = 0;

	lockdep_assert_held(&pon->lock);
	global = readl(pon->mac + 0x4008);
	act = readl(pon->mac + 0x4d1c) & readl(pon->mac + 0x4d20);
	plmp = readl(pon->mac + 0x4aa4) & readl(pon->mac + 0x4aa8);
	key_control = readl(pon->mac + 0x4ab8);
	transitions = readl(pon->mac + 0x4d28) & readl(pon->mac + 0x4d2c);
	state = readl(pon->mac + 0x4d30) & 7;
	writel(act, pon->mac + 0x4d1c);
	writel(plmp, pon->mac + 0x4aa4);
	writel(transitions, pon->mac + 0x4d28);
	pon->xgpon_irq_count++;
	pon->xgpon_last_irq = global;
	if (!pon->xgpon_enabled)
		return;
	was_active = pon->xgpon_last_state == 3 || pon->xgpon_last_state == 4;
	if (transitions)
		pon->xgpon_last_transitions = transitions;
	if (transitions & BIT(11))
		pon->xgpon_deactivations++;
	if ((was_active && state != 3 && state != 4) ||
	    (transitions & (BIT(1) | BIT(10) | BIT(11)))) {
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
	if (plmp & (BIT(5) | BIT(6) | BIT(7)))
		rtl_pon_xgpon_key_irq(pon, plmp, key_control);
	if (pon->xgpon_omcc_ready && state == 3 && pon->xgpon_key_ready)
		netif_carrier_on(pon->omci_device);
	else
		netif_carrier_off(pon->omci_device);
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
