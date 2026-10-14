// SPDX-License-Identifier: GPL-2.0-or-later
/* GPON MAC preparation; register values from this board's original ca-ne.ko. */
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/interrupt.h>
#include <linux/netdevice.h>
#include <linux/random.h>
#include <linux/unaligned.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-gpon-hw.h"

static unsigned int rtl_gpon_read(void *context, unsigned int reg)
{
	return readl(((struct rtl9617c_pon *)context)->mac + reg);
}

static void rtl_gpon_write(void *context, unsigned int reg, unsigned int value)
{
	writel(value, ((struct rtl9617c_pon *)context)->mac + reg);
}

static int rtl_gpon_indirect(void *context, unsigned int reg, unsigned int value)
{
	struct rtl9617c_pon *pon = context;
	u32 status;

	writel(value, pon->mac + reg);
	return readl_poll_timeout(pon->mac + reg, status, !(status & BIT(31)), 1, 100000);
}

ssize_t rtl_pon_gpon_identity(struct rtl9617c_pon *pon, const char *buf, size_t count)
{
	ssize_t ret = count;

	if (count != 18)
		return -EMSGSIZE;
	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (pon->mode != RTL_PON_GPON)
		ret = -EOPNOTSUPP;
	else {
		memcpy(pon->gpon_serial, buf, 8);
		memcpy(pon->gpon_password, buf + 8, 10);
		pon->gpon_identity_configured = true;
	}
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	return ret;
}

int rtl_pon_gpon_prepare_locked(struct rtl9617c_pon *pon)
{
	const struct rtl_dq_io io = {pon, rtl_gpon_read, rtl_gpon_write, rtl_gpon_indirect};
	u8 key[2][16];
	unsigned int index, bank, fragment;
	int ret = 0;

	lockdep_assert_held(&pon->control_lock);
	gpiod_set_value_cansleep(pon->tx_disable, 1);
	rtl_pon_gpon_stop_locked(pon);
	rtl_pon_xgpon_stop_locked(pon);
	rtl_pon_mpcp_stop_locked(pon);
	mutex_lock(&pon->lock);
	pon->mac_prepared = false;
	if (pon->mode != RTL_PON_GPON) {
		ret = -EOPNOTSUPP;
		goto unlock;
	}
	if (!pon->serdes_configured || !pon->gpon_identity_configured) {
		ret = -EAGAIN;
		goto unlock;
	}
	pon->puc_configured = false;
	pon->pdc_configured = false;
	pon->traffic_enabled = false;
	pon->gpon_omcc_onu_id = 255;
	pon->gpon_last_state = 0;
	/* GPON owns ANI+GPON; XGTC stays in reset. SerDes was configured first. */
	writel(0, pon->gpon_control);
	msleep(1);
	writel(0x30e, pon->control);
	writel(3, pon->gpon_control);
	msleep(1);
	rtl_gpon_active(&io, false);
	rtl_gpon_mac_parameters(&io);
	rtl_gpon_identity(&io, pon->gpon_serial, pon->gpon_password);
	/* A fresh MAC session starts with no upstream GEM binding. */
	for (index = 0; index < 256; index++) {
		ret = rtl_gpon_upstream_gem_set(&io, index, 0);
		if (ret)
			goto unlock;
	}
	get_random_bytes(key, sizeof(key));
	for (bank = 0; bank < 2; bank++)
		for (fragment = 0; fragment < 4; fragment++)
			writel(get_unaligned_le32(key[bank] + 12 - fragment * 4),
			       pon->mac + 0x6030 + bank * 16 + fragment * 4);
	memzero_explicit(key, sizeof(key));
	/* CRC and AES are GTC hardware functions; activation is a separate step. */
	rtl_pon_update(pon->mac + 0x6114, BIT(5) | BIT(6), BIT(5) | BIT(6));
	rtl_pon_update(pon->mac + 0x61c4, BIT(17), 0);
	pon->mac_prepared = true;
	dev_info(pon->dev, "GPON MAC identity, AES banks and upstream GEM table prepared\n");
unlock:
	pon->mac_error = ret;
	mutex_unlock(&pon->lock);
	return ret;
}

void rtl_pon_gpon_stop_locked(struct rtl9617c_pon *pon)
{
	const struct rtl_dq_io io = {pon, rtl_gpon_read, rtl_gpon_write, rtl_gpon_indirect};

	lockdep_assert_held(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (pon->mode != RTL_PON_GPON) {
		mutex_unlock(&pon->lock);
		return;
	}
	pon->gpon_enabled = false;
	pon->itu_line_epoch++;
	rtl_pon_itu_paths_invalidate(pon);
	pon->gpon_omcc_ready = false;
	writel(0, pon->mac + 0x6088);
	writel(0, pon->mac + 0x6090);
	rtl_pon_update(pon->top_irq + 4, BIT(0), 0);
	rtl_gpon_active(&io, false);
	gpiod_set_value_cansleep(pon->tx_disable, 1);
	if (pon->omci_device)
		netif_carrier_off(pon->omci_device);
	mutex_unlock(&pon->lock);
	synchronize_irq(pon->mpcp_irq);
}

int rtl_pon_gpon_activation_locked(struct rtl9617c_pon *pon, bool enable)
{
	const struct rtl_dq_io io = {pon, rtl_gpon_read, rtl_gpon_write, rtl_gpon_indirect};
	unsigned int bank;
	int ret = 0;

	lockdep_assert_held(&pon->control_lock);
	if (!enable) {
		rtl_pon_gpon_stop_locked(pon);
		return 0;
	}
	mutex_lock(&pon->lock);
	if (pon->mode != RTL_PON_GPON) {
		ret = -EOPNOTSUPP;
		goto unlock;
	}
	if (!pon->mac_prepared || !pon->puc_configured || !pon->pdc_configured ||
	    !netif_running(pon->omci_device)) {
		ret = -EAGAIN;
		goto unlock;
	}
	writel(0, pon->mac + 0x6088);
	for (bank = 0; bank < 4; bank++) {
		writel(0, pon->mac + 0x6090 + bank * 8);
		writel(readl(pon->mac + 0x608c + bank * 8), pon->mac + 0x608c + bank * 8);
	}
	pon->gpon_omcc_ready = false;
	pon->gpon_omcc_onu_id = 255;
	pon->gpon_activation_error = 0;
	pon->gpon_enabled = true;
	writel(0xc00affff, pon->mac + 0x6090);
	writel(1, pon->mac + 0x6088);
	rtl_pon_update(pon->top_irq + 4, BIT(0), BIT(0));
	rtl_pon_update(pon->serdes_init, BIT(4), BIT(4));
	gpiod_set_value_cansleep(pon->tx_disable, 0);
	/* The GTC hardware owns serial-number exchange, ranging and state changes. */
	rtl_gpon_active(&io, true);
unlock:
	mutex_unlock(&pon->lock);
	return ret;
}

void rtl_pon_gpon_irq(struct rtl9617c_pon *pon)
{
	const struct rtl_dq_io io = {pon, rtl_gpon_read, rtl_gpon_write, rtl_gpon_indirect};
	u32 flags = readl(pon->mac + 0x608c) & readl(pon->mac + 0x6090);
	u32 onu = readl(pon->mac + 0x60bc);
	unsigned int state = (onu >> 16) & 7, id = onu & 255, index;
	u32 port = readl(pon->mac + 0x60c8);
	int ret = 0;

	lockdep_assert_held(&pon->lock);
	writel(flags, pon->mac + 0x608c);
	pon->gpon_last_irq = flags;
	pon->gpon_irq_count++;
	if (!pon->gpon_enabled)
		return;
	if (flags & BIT(14))
		pon->gpon_ds_fec = true;
	if (flags & BIT(15))
		pon->gpon_ds_fec = false;
	if (flags & BIT(19)) {
		u8 key[16];

		get_random_bytes(key, sizeof(key));
		rtl_gpon_next_key(&io, key);
		memzero_explicit(key, sizeof(key));
		pon->gpon_key_switches++;
	}
	if ((flags & BIT(30)) && id != 255) {
		if (pon->gpon_omcc_onu_id != 255 && pon->gpon_omcc_onu_id != id) {
			ret = rtl_gpon_tcont_set(&io, pon->gpon_omcc_onu_id, 0, false, false, false);
			if (ret)
				goto status;
			pon->gpon_omcc_ready = false;
		}
		ret = rtl_gpon_tcont_set(&io, id, 0, true, true, false);
		if (ret)
			goto status;
		pon->gpon_omcc_onu_id = id;
	}
	if ((flags & (BIT(17) | BIT(31))) && state == 4 && (port & BIT(12)) &&
	    pon->gpon_omcc_onu_id == id &&
	    (!pon->gpon_omcc_ready || pon->gpon_omcc_gem != (port & 4095))) {
		pon->gpon_omcc_ready = false;
		for (index = 0; index < 8; index++) {
			ret = rtl_gpon_upstream_gem_set(&io, index, port & 4095);
			if (ret)
				goto status;
		}
		pon->gpon_omcc_gem = port & 4095;
		pon->gpon_omcc_ready = true;
	}
status:
	if (state != 4 && state != 5)
		pon->gpon_omcc_ready = false;
	if (ret || (flags & (BIT(17) | BIT(30) | BIT(31))))
		pon->gpon_activation_error = ret;
	if (pon->gpon_omcc_ready && state == 4 && !pon->gpon_activation_error)
		netif_carrier_on(pon->omci_device);
	else
		netif_carrier_off(pon->omci_device);
	if (state == 6 || pon->gpon_last_state == 6)
		gpiod_set_value_cansleep(pon->tx_disable, state == 6);
	if ((pon->gpon_last_state == 4 || pon->gpon_last_state == 5) && state != 4 && state != 5)
		pon->itu_line_epoch++;
	if (!netif_carrier_ok(pon->omci_device) && pon->itu_paths_ready)
		rtl_pon_itu_paths_invalidate(pon);
	rtl_pon_itu_carrier(pon);
	if (state != pon->gpon_last_state) {
		char value[32], identity[32];
		char *env[] = {"PON_EVENT=activation", value, identity, NULL};

		snprintf(value, sizeof(value), "PON_STATE=O%u", state + 1);
		snprintf(identity, sizeof(identity), "PON_ONU_ID=%u", id);
		kobject_uevent_env(&pon->dev->kobj, KOBJ_CHANGE, env);
		dev_info(pon->dev, "GPON O%u -> O%u ONU-ID=%u OMCC=%u error=%d\n",
			pon->gpon_last_state + 1, state + 1, id, pon->gpon_omcc_ready, ret);
	}
	pon->gpon_last_state = state;
}
