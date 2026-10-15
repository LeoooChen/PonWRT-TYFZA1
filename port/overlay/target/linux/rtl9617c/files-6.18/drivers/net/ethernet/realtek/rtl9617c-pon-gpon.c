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
	int ret = readl_poll_timeout(pon->mac + reg, status, !(status & BIT(31)), 1, 100000);

	if (ret)
		return ret;
	writel(value, pon->mac + reg);
	return readl_poll_timeout(pon->mac + reg, status, !(status & BIT(31)), 1, 100000);
}

static int rtl_gpon_drain_map(struct rtl9617c_pon *pon, bool restore)
{
	struct rtl_gpon_drain *drain = &pon->gpon_drain;
	u32 status;
	unsigned int bit = drain->queue * 9 + 8;
	int ret = readl_poll_timeout(pon->mac + 0x8000, status, !(status & BIT(31)), 1, 100000);

	if (ret)
		return ret;
	for (unsigned int i = 0; i < 5; i++) {
		u32 word = drain->pvt[i];
		if (!restore && i == bit / 32)
			word &= ~BIT(bit % 32);
		writel(word, pon->mac + 0x8014 - i * 4);
	}
	return rtl_gpon_indirect(pon, 0x8000, 0xc0000000U | drain->port);
}

static int rtl_gpon_drain_alloc(struct rtl9617c_pon *pon, bool restore)
{
	struct rtl_gpon_drain *drain = &pon->gpon_drain;
	u32 word;
	int ret;

	if (!drain->alloc_saved)
		return 0;
	ret = rtl_gpon_indirect(pon, 0x612c, 0x80000000U | drain->alloc_id);
	if (ret)
		return ret;
	word = readl(pon->mac + 0x6130) & ~BIT(1);
	if (restore)
		word |= drain->alloc_word & BIT(1);
	writel(word, pon->mac + 0x6130);
	return rtl_gpon_indirect(pon, 0x612c, 0xc0000000U | drain->alloc_id);
}

static int rtl_gpon_drain_count(struct rtl9617c_pon *pon, unsigned int queue)
{
	struct rtl_gpon_drain *drain = &pon->gpon_drain;
	u32 high, low;
	int ret = rtl_gpon_indirect(pon, 0x802c, 0x80000000U | (drain->port * 8 + queue));

	if (ret)
		return ret;
	high = readl(pon->mac + 0x8034);
	low = readl(pon->mac + 0x8030);
	drain->bytes[queue] = high & 0x1fffff;
	drain->packets[queue] = (high >> 21) | ((low & 7) << 11);
	return 0;
}

static int rtl_gpon_drain_probe(struct rtl9617c_pon *pon)
{
	static const u8 header[16] = {0, 1, 2, 3, 4, 5, 0, 0x11, 0x22, 0x33, 0x44, 0x55, 0x81, 0, 1, 0x40};
	struct rtl_gpon_drain *drain = &pon->gpon_drain;
	struct rtl9617c_ni_frame frame = {
		.header_high = BIT(13) | (8 << 18),
		.header_low = BIT(29) | (17 << 9) | ((32 + drain->port) << 3) | drain->probe,
	};
	struct sk_buff *skb = alloc_skb(64, GFP_KERNEL);

	if (!skb)
		return -ENOMEM;
	/* The original initializes only16 bytes of its64-byte flush probe. */
	memset(skb_put(skb, 64), 0, 64);
	memcpy(skb->data, header, sizeof(header));
	if (rtl9617c_ni_transmit(pon->ni_dev, pon->omci_device, skb, &frame) == NETDEV_TX_BUSY) {
		dev_kfree_skb_any(skb);
		return -EAGAIN;
	}
	return 0;
}

/* GPON uses PUC queue flushes and GTC pulses, not the XGTC MAC/PUC reset. */
static int rtl_gpon_drain_run(struct rtl9617c_pon *pon)
{
	struct rtl_gpon_drain *drain = &pon->gpon_drain;
	u32 status;
	unsigned int reg, shift;
	int ret;

	for (;;) switch (drain->phase) {
	case RTL_GPON_DRAIN_IDLE:
		drain->config = readl(pon->mac + 0x808c) & BIT(30);
		drain->drop = readl(pon->mac + 0x80e0) & (BIT(31) | 255);
		drain->gtc = readl(pon->mac + 0x61c4) & (BIT(31) | GENMASK(28, 24));
		for (unsigned int i = 0; i < 8; i++)
			drain->valid[i] = readl(pon->mac + 0x81bc - i * 4);
		rtl_pon_update(pon->mac + 0x808c, BIT(30), BIT(30));
		rtl_pon_update(pon->mac + 0x80e0, BIT(31) | 255, BIT(31) | 6);
		for (unsigned int i = 0; i < 8; i++)
			writel(0, pon->mac + 0x81bc - i * 4);
		udelay(250);
		drain->port = 0;
		drain->phase = RTL_GPON_DRAIN_PORT;
		break;
	case RTL_GPON_DRAIN_PORT:
		if (drain->port == 32) {
			drain->phase = RTL_GPON_DRAIN_RESET;
			break;
		}
		ret = rtl_gpon_indirect(pon, 0x8000, 0x80000000U | drain->port);
		if (ret) return ret;
		for (unsigned int i = 0; i < 5; i++)
			drain->pvt[i] = readl(pon->mac + 0x8014 - i * 4);
		drain->alloc_saved = drain->port ? !!(pon->itu_alloc_valid & BIT(drain->port)) :
			pon->gpon_omcc_onu_id != 255;
		if (drain->alloc_saved) {
			drain->alloc_id = drain->port ? pon->itu_alloc_ids[drain->port] : pon->gpon_omcc_onu_id;
			ret = rtl_gpon_indirect(pon, 0x612c, 0x80000000U | drain->alloc_id);
			if (ret) return ret;
			drain->alloc_word = readl(pon->mac + 0x6130);
		}
		drain->phase = RTL_GPON_DRAIN_ALLOC;
		break;
	case RTL_GPON_DRAIN_ALLOC:
		ret = rtl_gpon_drain_alloc(pon, false);
		if (ret) return ret;
		msleep(1);
		drain->probe = 8;
		for (unsigned int i = 0; i < 8; i++) {
			ret = rtl_gpon_drain_count(pon, i);
			if (ret) return ret;
			if (drain->bytes[i] && !drain->packets[i]) drain->probe = i;
		}
		drain->phase = RTL_GPON_DRAIN_PROBE;
		break;
	case RTL_GPON_DRAIN_PROBE:
		if (drain->probe < 8) {
			reg = 0x81bc - drain->port / 4 * 4;
			rtl_pon_update(pon->mac + reg, BIT(drain->port % 4 * 8 + drain->probe),
				BIT(drain->port % 4 * 8 + drain->probe));
			ret = rtl_gpon_drain_probe(pon);
			if (ret) {
				rtl_pon_update(pon->mac + reg, BIT(drain->port % 4 * 8 + drain->probe), 0);
				return ret;
			}
		}
		drain->phase = RTL_GPON_DRAIN_PROBE_WAIT;
		break;
	case RTL_GPON_DRAIN_PROBE_WAIT:
		if (drain->probe < 8) {
			msleep(1);
			ret = rtl_gpon_drain_count(pon, drain->probe);
			if (ret) return ret;
			if (drain->bytes[drain->probe] && !drain->packets[drain->probe]) return -EAGAIN;
			reg = 0x81bc - drain->port / 4 * 4;
			rtl_pon_update(pon->mac + reg, BIT(drain->port % 4 * 8 + drain->probe), 0);
		}
		drain->queue = 0;
		drain->phase = RTL_GPON_DRAIN_QUEUE;
		break;
	case RTL_GPON_DRAIN_QUEUE:
		drain->limit = readl(pon->mac + 0x8090 + drain->port / 2 * 4);
		rtl_pon_update(pon->mac + 0x80e0, BIT(31) | 255, BIT(31) | (drain->port * 8 + drain->queue));
		drain->phase = RTL_GPON_DRAIN_MAP;
		break;
	case RTL_GPON_DRAIN_MAP:
		ret = rtl_gpon_drain_map(pon, false);
		if (ret) return ret;
		shift = drain->port % 2 * 16 + drain->queue * 2;
		rtl_pon_update(pon->mac + 0x8090 + drain->port / 2 * 4, 3U << shift, 0);
		drain->phase = RTL_GPON_DRAIN_GTC;
		break;
	case RTL_GPON_DRAIN_GTC:
		if (!drain->queue) {
			rtl_pon_update(pon->mac + 0x61c4, BIT(31) | GENMASK(28, 24), BIT(31) | (drain->port << 24));
			udelay(250);
			rtl_pon_update(pon->mac + 0x61c4, BIT(31), 0);
		}
		drain->phase = RTL_GPON_DRAIN_FLUSH;
		break;
	case RTL_GPON_DRAIN_FLUSH:
		if (drain->packets[drain->queue]) {
			ret = readl_poll_timeout(pon->mac + 0x80dc, status, !(status & BIT(31)), 1000, 1000000);
			if (ret) return ret;
			rtl_pon_update(pon->mac + 0x80dc, BIT(31) | BIT(16) | GENMASK(12, 0),
				BIT(31) | BIT(16) | (drain->port << 8) | (drain->port * 8 + drain->queue));
		}
		drain->phase = RTL_GPON_DRAIN_FLUSH_WAIT;
		break;
	case RTL_GPON_DRAIN_FLUSH_WAIT:
		ret = readl_poll_timeout(pon->mac + 0x80dc, status, !(status & BIT(31)), 1000, 1000000);
		if (ret) return ret;
		ret = rtl_gpon_drain_count(pon, drain->queue);
		if (ret) return ret;
		if (drain->bytes[drain->queue] || drain->packets[drain->queue]) return -EIO;
		drain->phase = RTL_GPON_DRAIN_RESTORE;
		break;
	case RTL_GPON_DRAIN_RESTORE:
		ret = rtl_gpon_drain_map(pon, true);
		if (ret) return ret;
		writel(drain->limit, pon->mac + 0x8090 + drain->port / 2 * 4);
		rtl_pon_update(pon->mac + 0x80e0, BIT(31), 0);
		drain->phase = ++drain->queue == 8 ? RTL_GPON_DRAIN_PORT_RESTORE : RTL_GPON_DRAIN_QUEUE;
		break;
	case RTL_GPON_DRAIN_PORT_RESTORE:
		ret = rtl_gpon_drain_alloc(pon, true);
		if (ret) return ret;
		drain->port++;
		drain->phase = RTL_GPON_DRAIN_PORT;
		break;
	case RTL_GPON_DRAIN_RESET:
		udelay(125);
		rtl_pon_update(pon->mac + 0x6114, BIT(4), BIT(4));
		udelay(250);
		rtl_pon_update(pon->mac + 0x6114, BIT(4), 0);
		rtl_pon_update(pon->mac + 0x61c4, BIT(31) | GENMASK(28, 24), drain->gtc);
		rtl_pon_update(pon->mac + 0x80e0, BIT(31) | 255, drain->drop);
		for (unsigned int i = 0; i < 8; i++)
			writel(drain->valid[i], pon->mac + 0x81bc - i * 4);
		rtl_pon_update(pon->mac + 0x808c, BIT(30), drain->config);
		drain->phase = RTL_GPON_DRAIN_IDLE;
		return 0;
	}
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
	if (pon->mode != RTL_PON_GPON)
		return -EOPNOTSUPP;
	ret = rtl_pon_stop_locked(pon);
	mutex_lock(&pon->lock);
	pon->mac_prepared = false;
	if (ret)
		goto unlock;
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
	bool recovering;

	lockdep_assert_held(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (pon->mode != RTL_PON_GPON) {
		mutex_unlock(&pon->lock);
		return;
	}
	recovering = pon->gpon_recover;
	pon->gpon_enabled = false;
	pon->gpon_recover = false;
	cancel_delayed_work(&pon->gpon_recovery_work);
	pon->itu_line_epoch++;
	rtl_pon_itu_paths_invalidate(pon, pon->mode);
	pon->gpon_omcc_ready = false;
	writel(0, pon->mac + 0x6088);
	writel(0, pon->mac + 0x6090);
	rtl_pon_update(pon->top_irq + 4, BIT(0), 0);
	rtl_gpon_active(&io, false);
	gpiod_set_value_cansleep(pon->tx_disable, 1);
	if (recovering) {
		/* Explicit stop aborts the owned PUC/GTC transaction, as deinit does. */
		writel(0, pon->gpon_control);
		rtl_pon_update(pon->control, BIT(8) | BIT(9), 0);
		pon->mac_prepared = false;
		pon->puc_configured = false;
		pon->pdc_configured = false;
		pon->gpon_drain.phase = RTL_GPON_DRAIN_IDLE;
	}
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
	if (pon->gpon_recover) {
		ret = -EBUSY;
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
	/* Keep the pending GPON events latched until the drain owns no tables. */
	if (pon->gpon_recover)
		return;
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
	if ((pon->gpon_last_state == 4 || pon->gpon_last_state == 5) && state != 4 && state != 5) {
		pon->itu_line_epoch++;
		pon->gpon_recover = true;
		rtl_pon_update(pon->top_irq + 4, BIT(0), 0);
		schedule_delayed_work(&pon->gpon_recovery_work, 0);
	}
	/* O6 pauses carrier while retaining the established business mapping. */
	if (pon->itu_paths_ready && (!pon->gpon_omcc_ready || pon->gpon_activation_error ||
	    (state != 4 && state != 5)))
		rtl_pon_itu_paths_invalidate(pon, pon->mode);
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

void rtl_pon_gpon_recovery(struct work_struct *work)
{
	struct rtl9617c_pon *pon = container_of(to_delayed_work(work), struct rtl9617c_pon, gpon_recovery_work);
	int ret;

	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (!pon->gpon_recover)
		goto unlock;
	ret = pon->gpon_drain.phase ? 0 : rtl_pon_itu_paths_invalidate(pon, pon->mode);
	if (!ret)
		ret = rtl_gpon_drain_run(pon);
	pon->gpon_activation_error = ret;
	if (ret) {
		schedule_delayed_work(&pon->gpon_recovery_work, HZ);
	} else {
		pon->gpon_recover = false;
		rtl_pon_update(pon->top_irq + 4, BIT(0), BIT(0));
	}
unlock:
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
}
