// SPDX-License-Identifier: GPL-2.0-or-later
/* Native GPON/XGTC control IO, verified against TYFZA1 ca-ne.ko. */
#include <linux/bitfield.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-gpon-hw.h"
#include "rtl9617c-xgpon-hw.h"

static unsigned int rtl_itu_read(void *context, unsigned int reg)
{
	struct rtl9617c_pon *pon = context;

	return readl(pon->mac + reg);
}

static void rtl_itu_write(void *context, unsigned int reg, unsigned int value)
{
	struct rtl9617c_pon *pon = context;

	writel(value, pon->mac + reg);
}

ssize_t rtl_pon_itu_state(struct rtl9617c_pon *pon, char *buf)
{
	static const char *const gpon_states[] = {"O1", "O2", "O3", "O4", "O5", "O6", "O7", "reserved"};
	static const char *const xgpon_states[] = {"O1", "O23", "O4", "O5", "O6", "O7", "O8", "O9"};
	u32 word, state;

	lockdep_assert_held(&pon->lock);
	if (pon->mode == RTL_PON_GPON) {
		word = readl(pon->mac + 0x60bc);
		state = FIELD_GET(GENMASK(18, 16), word);
		return sysfs_emit(buf,
			"prepared=%u error=%d protocol=gpon activation=%s hardware_state=%u onu_id=%u onu=%08x control=%08x config=%08x rx=%llu tx=%llu enabled=%u omcc=%u irq_count=%llu key_switches=%llu\n",
			pon->mac_prepared, pon->mac_error, gpon_states[state], state,
			word & 255, word, readl(pon->mac + 0x6114), readl(pon->mac + 0x6118),
			pon->ploam_rx, pon->ploam_tx, pon->gpon_enabled, pon->gpon_omcc_ready,
			pon->gpon_irq_count, pon->gpon_key_switches);
	}
	word = readl(pon->mac + 0x4d30);
	state = word & 7;
	return sysfs_emit(buf,
		"prepared=%u error=%d protocol=%s activation=%s hardware_state=%u sub_state=%u previous_state=%u actp=%08x rx0=%08x rx1=%08x rx=%llu tx=%llu mic_errors=%llu control=%08x upstream_rate=%u\n",
		pon->mac_prepared, pon->mac_error, rtl_pon_mode_name(pon->mode),
		xgpon_states[state], state, !!(word & BIT(4)), (u32)FIELD_GET(GENMASK(10, 8), word), word,
		readl(pon->mac + 0x4b28), readl(pon->mac + 0x4b2c),
		pon->ploam_rx, pon->ploam_tx, pon->ploam_mic_errors, readl(pon->mac + 0x4004),
		(u32)FIELD_GET(GENMASK(7, 6), readl(pon->mac + 0x4004)));
}

ssize_t rtl_pon_ploam_receive(struct rtl9617c_pon *pon, char *buf, size_t count)
{
	const struct rtl_dq_io io = {pon, rtl_itu_read, rtl_itu_write, NULL};
	unsigned char order[2];
	u32 status[2], received_status;
	unsigned int ready;
	ssize_t ret;

	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (rtl_pon_mode_is_epon(pon->mode)) {
		ret = -EOPNOTSUPP;
	} else if (pon->mode == RTL_PON_GPON) {
		if (count < RTL_GPON_PLOAM_BYTES) {
			ret = -EMSGSIZE;
		} else {
			rtl_gpon_ploam_receive(&io, (u8 *)buf);
			ret = buf[1] ? RTL_GPON_PLOAM_BYTES : -EAGAIN;
		}
	} else if (count < RTL_XGPON_PLOAM_BYTES) {
		ret = -EMSGSIZE;
	} else if (pon->xgpon_pending_valid) {
		memcpy(buf, pon->xgpon_pending_ploam, RTL_XGPON_PLOAM_BYTES);
		pon->xgpon_pending_valid = false;
		ret = RTL_XGPON_PLOAM_BYTES;
		if (pon->xgpon_pending_status & BIT(15)) {
			pon->ploam_mic_errors++;
			ret = -EBADMSG;
		}
	} else {
		status[0] = readl(pon->mac + 0x4b28);
		status[1] = readl(pon->mac + 0x4b2c);
		ready = rtl_xgpon_ploam_order(status[0], status[1], order);
		if (!ready) {
			ret = -EAGAIN;
		} else {
			rtl_xgpon_ploam_receive(&io, order[0], (u8 *)buf);
			received_status = status[order[0]];
			if (ready == 2) {
				/* Snapshot both old packets before accepting a refill. */
				rtl_xgpon_ploam_receive(&io, order[1], pon->xgpon_pending_ploam);
				pon->xgpon_pending_status = status[order[1]];
				pon->xgpon_pending_valid = true;
			}
			if (received_status & BIT(15)) {
				pon->ploam_mic_errors++;
				ret = -EBADMSG;
			} else {
				ret = RTL_XGPON_PLOAM_BYTES;
			}
		}
	}
	if (ret > 0)
		pon->ploam_rx++;
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	return ret;
}

ssize_t rtl_pon_ploam_transmit(struct rtl9617c_pon *pon, const char *buf, size_t count,
			     bool default_key)
{
	const struct rtl_dq_io io = {pon, rtl_itu_read, rtl_itu_write, NULL};
	u32 value;
	ssize_t ret;

	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (rtl_pon_mode_is_epon(pon->mode) || (pon->mode == RTL_PON_GPON && default_key)) {
		ret = -EOPNOTSUPP;
	} else if (!pon->mac_prepared || (pon->mode == RTL_PON_GPON && !(readl(pon->mac + 0x6114) & BIT(1)))) {
		ret = -EAGAIN;
	} else if (pon->mode == RTL_PON_GPON) {
		if (count != 12 || (unsigned char)buf[0] != (readl(pon->mac + 0x60bc) & 255)) {
			ret = -EINVAL;
		} else {
			ret = readl_poll_timeout(pon->mac + 0x60e0, value, !(value & BIT(0)), 1, 10000);
			if (!ret) {
				rtl_gpon_ploam_transmit(&io, buf[1], (const u8 *)buf + 2);
				ret = readl_poll_timeout(pon->mac + 0x60e0, value, !(value & BIT(0)), 1, 10000);
			}
		}
	} else if (count != RTL_XGPON_PLOAM_TX_BYTES) {
		ret = -EMSGSIZE;
	} else {
		ret = readl_poll_timeout(pon->mac + 0x4afc, value, !(value & BIT(31)), 1, 10000);
		if (!ret) {
			rtl_xgpon_ploam_transmit(&io, (const u8 *)buf, default_key);
			ret = readl_poll_timeout(pon->mac + 0x4afc, value, !(value & BIT(31)), 1, 10000);
		}
	}
	if (!ret) {
		pon->ploam_tx++;
		ret = count;
	}
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	return ret;
}
