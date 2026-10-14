// SPDX-License-Identifier: GPL-2.0-or-later
/* ITU business configuration follows original ca_gpon_port_{tcont_set,gem_port_add}. */
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/rcupdate.h>
#include <linux/slab.h>
#include <linux/unaligned.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-gpon-hw.h"
#include "rtl9617c-xgpon-hw.h"
#include "rtl9617c-pon-itu-puc.h"

static unsigned int path_read(void *context, unsigned int address)
{
	return readl(((struct rtl9617c_pon *)context)->mac + address);
}

static void path_write(void *context, unsigned int address, unsigned int value)
{
	writel(value, ((struct rtl9617c_pon *)context)->mac + address);
}

static int path_access(void *context, unsigned int address, unsigned int value)
{
	struct rtl9617c_pon *pon = context;
	u32 status;

	writel(value, pon->mac + address);
	return readl_poll_timeout(pon->mac + address, status, !(status & BIT(31)), 1, 100000);
}

void rtl_pon_itu_carrier(struct rtl9617c_pon *pon)
{
	struct net_device *dev;

	rcu_read_lock();
	dev = rcu_dereference(pon->data_devices[0]);
	if (dev) {
		if (pon->itu_paths_ready && netif_carrier_ok(pon->omci_device))
			netif_carrier_on(dev);
		else
			netif_carrier_off(dev);
	}
	rcu_read_unlock();
}

void rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *pon)
{
	struct net_device *dev = rcu_access_pointer(pon->data_devices[0]);

	lockdep_assert_held(&pon->lock);
	pon->itu_paths_ready = false;
	pon->itu_path_epoch++;
	if (dev) {
		netif_carrier_off(dev);
		pon->itu_paths_error = rtl9617c_ni_itu_paths(pon->ni_dev, dev, NULL);
	}
}

static int path_program(struct rtl9617c_pon *pon, const struct rtl_itu_paths *paths)
{
	const struct rtl_dq_io io = {pon, path_read, path_write, path_access};
	u32 queues[8] = {255}, active = 0;
	unsigned int i, index;
	bool gpon = pon->mode == RTL_PON_GPON;
	int ret;

	for (index = 1; index < 32; index++) {
		ret = rtl_itu_puc_tcont(&io, index, 0);
		if (ret) return ret;
		if (gpon) {
			if (!(pon->itu_alloc_valid & BIT(index))) continue;
			ret = rtl_gpon_tcont_set(&io, pon->itu_alloc_ids[index], 0, false, false, true);
			if (ret) return ret;
			pon->itu_alloc_valid &= ~BIT(index);
		} else {
			u32 status;

			/* Original T-CONT removal flushes USBG before releasing Alloc-ID. */
			rtl_pon_update(pon->mac + 0x4004, GENMASK(28, 24) | GENMASK(11, 8) | BIT(31),
				(index << 24) | BIT(31));
			ret = readl_poll_timeout(pon->mac + 0x4004, status, !(status & BIT(31)), 1, 100000);
			if (ret) return ret;
			ret = rtl_xgpon_tcont_set(&io, index, RTL_XGPON_ALLOC_ID_INVALID, false, false);
			if (ret) return ret;
		}
	}
	if (gpon) for_each_set_bit(index, pon->itu_gpon_gems, 4096) {
		ret = rtl_gpon_downstream_gem_set(&io, index, 0, false, false, false);
		if (ret) return ret;
		__clear_bit(index, pon->itu_gpon_gems);
	}
	for (index = 8; index < 256; index++) {
		if (gpon) {
			ret = rtl_gpon_upstream_gem_set(&io, index, 0);
			if (ret) return ret;
		} else {
			rtl_xgpon_downstream_gem_set(&io, index, 0, false, false);
			ret = rtl_xgpon_upstream_gem_set(&io, index, 0, false, false, false);
			if (ret) return ret;
		}
	}
	for (i = 0; i < paths->count; i++) {
		const struct rtl_itu_path *path = &paths->path[i];

		index = path->index;
		if (!path->multicast && !(active & BIT(path->tcont))) {
			if (gpon) {
				pon->itu_alloc_ids[path->tcont] = path->alloc_id;
				pon->itu_alloc_valid |= BIT(path->tcont);
				ret = rtl_gpon_tcont_set(&io, path->alloc_id, path->tcont, true, true, true);
			} else {
				ret = rtl_xgpon_tcont_set(&io, path->tcont, path->alloc_id, true, true);
			}
			if (ret) return ret;
			ret = rtl_itu_puc_tcont(&io, path->tcont, 255);
			if (ret) return ret;
			queues[path->tcont / 4] |= 255U << ((path->tcont % 4) * 8);
			active |= BIT(path->tcont);
		}
		if (gpon) {
			/* GTC keeps PLOAM's AES decision in the hardware table. */
			__set_bit(path->gem_id, pon->itu_gpon_gems);
			ret = rtl_gpon_downstream_gem_set(&io, path->gem_id, index, true, false, false);
			if (ret) return ret;
			if (!path->multicast) {
				ret = rtl_gpon_upstream_gem_set(&io, index, path->gem_id);
				if (ret) return ret;
			}
		} else {
			rtl_xgpon_downstream_gem_set(&io, index, path->gem_id, true, path->multicast);
			ret = rtl_xgpon_upstream_gem_set(&io, index, path->gem_id,
				!path->multicast, path->encrypted, path->multicast);
			if (ret) return ret;
		}
		/* Original GEM add uses its internal index as disabled-policer metadata. */
		writel(((pon->fe_enabled ? 24 : 16) << 3) | (7 << 9) |
			(pon->fe_enabled ? 0 : BIT(15)), pon->mac + 0x9028);
		writel(index << 4, pon->mac + 0x9024);
		ret = path_access(pon, 0x9020, 0xc0000000U | index);
		if (ret) return ret;
	}
	for (i = 0; i < 8; i++) writel(queues[i], pon->mac + 0x81bc - i * 4);
	return 0;
}

ssize_t rtl_pon_itu_paths_write(struct rtl9617c_pon *pon, const char *buf, size_t count)
{
	struct rtl_itu_path_state *state, *old;
	struct net_device *dev;
	unsigned int i, j, entries, unique = 0;
	int ret = 0;

	if (count < 4 || get_unaligned_le16(buf) != 2) return -EINVAL;
	entries = get_unaligned_le16(buf + 2);
	if (entries > RTL_ITU_PATH_MAX || count != 4 + entries * RTL_ITU_PATH_WIRE_BYTES) return -EMSGSIZE;
	state = kzalloc(sizeof(*state), GFP_KERNEL);
	if (!state) return -ENOMEM;
	state->paths.count = entries;
	for (i = 0; i < entries; i++) {
		const u8 *record = (const u8 *)buf + 4 + i * RTL_ITU_PATH_WIRE_BYTES;
		struct rtl_itu_path *path = &state->paths.path[i];

		path->alloc_id = get_unaligned_le16(record);
		path->gem_id = get_unaligned_le16(record + 2);
		path->vlan = get_unaligned_le16(record + 4);
		path->pbits = record[6]; path->multicast = record[7];
		path->queue = record[8]; path->encrypted = record[9];
		path->tcont = get_unaligned_le16(record + 10);
		path->downstream_pbits = record[12];
		if ((path->vlan < RTL_ITU_VLAN_TAGGED && path->vlan > 4094) || path->multicast > 1 ||
		    path->encrypted > 1 || path->queue > 7 || (!path->multicast &&
		    (!(path->pbits | path->downstream_pbits) || !path->tcont || path->tcont > 31 || path->alloc_id > 16383))) {
			ret = -EINVAL; goto free;
		}
		path->index = 8 + unique;
		for (j = 0; j < i; j++) {
			const struct rtl_itu_path *previous = &state->paths.path[j];

			if (!path->multicast && !previous->multicast &&
			    ((path->tcont == previous->tcont && path->alloc_id != previous->alloc_id) ||
			    (path->vlan == previous->vlan && (path->pbits & previous->pbits) &&
			    path->gem_id != previous->gem_id))) { ret = -EINVAL; goto free; }
			if (previous->gem_id == path->gem_id) {
				if (previous->tcont != path->tcont || previous->queue != path->queue ||
				    previous->multicast != path->multicast || previous->encrypted != path->encrypted) {
					ret = -EINVAL; goto free;
				}
				path->index = previous->index;
			}
		}
		if (path->index == 8 + unique) unique++;
	}
	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (rtl_pon_mode_is_epon(pon->mode) || !pon->puc_configured || !pon->pdc_configured) {
		ret = -EOPNOTSUPP; goto unlock;
	}
	if (entries && !netif_carrier_ok(pon->omci_device)) { ret = -EAGAIN; goto unlock; }
	for (i = 0; i < entries; i++)
		if (pon->mode == RTL_PON_GPON && (state->paths.path[i].gem_id > 4095 ||
		    (!state->paths.path[i].multicast && state->paths.path[i].alloc_id > 4095))) {
			ret = -EINVAL; goto unlock;
		}
	old = rcu_dereference_protected(pon->itu_paths, lockdep_is_held(&pon->lock));
	if (pon->itu_paths_ready && old && !memcmp(&old->paths, &state->paths, sizeof(state->paths)))
		goto unlock;
	dev = rcu_access_pointer(pon->data_devices[0]);
	netif_tx_disable(dev);
	rtl_pon_itu_paths_invalidate(pon);
	ret = pon->itu_paths_error;
	if (ret) goto wake;
	ret = path_program(pon, &state->paths);
	if (ret) goto wake;
	ret = rtl9617c_ni_itu_paths(pon->ni_dev, dev, &state->paths);
	if (ret) goto wake;
	old = rcu_replace_pointer(pon->itu_paths, state, lockdep_is_held(&pon->lock));
	if (old) kfree_rcu(old, rcu);
	state = NULL;
	pon->itu_paths_ready = entries != 0;
	rtl_pon_itu_carrier(pon);
wake:
	pon->itu_paths_error = ret;
	if (netif_running(dev)) netif_tx_wake_all_queues(dev);
unlock:
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
free:
	kfree(state);
	return ret ? ret : count;
}

ssize_t rtl_pon_itu_paths_state(struct rtl9617c_pon *pon, char *buf)
{
	struct rtl_itu_path_state *state;
	unsigned int i;
	ssize_t length;

	lockdep_assert_held(&pon->lock);
	state = rcu_dereference_protected(pon->itu_paths, lockdep_is_held(&pon->lock));
	length = sysfs_emit(buf, "configured=%u error=%d epoch=%llu paths=%u session=%llu\n",
		pon->itu_paths_ready, pon->itu_paths_error, pon->itu_path_epoch,
		state ? state->paths.count : 0, pon->itu_line_epoch);
	if (state) for (i = 0; i < state->paths.count && length < PAGE_SIZE - 100; i++) {
		const struct rtl_itu_path *path = &state->paths.path[i];

		length += sysfs_emit_at(buf, length, "gem=%u index=%u alloc=%u tcont=%u queue=%u vlan=%u pbits=%02x multicast=%u upstream_aes=%u downstream_pbits=%02x\n",
			path->gem_id, path->index, path->alloc_id, path->tcont, path->queue,
			path->vlan, path->pbits, path->multicast, path->encrypted, path->downstream_pbits);
	}
	return length;
}

netdev_tx_t rtl_pon_itu_data_transmit(struct rtl9617c_pon *pon, struct sk_buff *skb, struct net_device *dev)
{
	struct rtl_itu_path_state *state;
	const struct rtl_itu_path *path;
	struct rtl9617c_ni_frame frame = {.header_high = BIT(30), .header_low = (16 << 9) | BIT(29)};
	u16 tci = 0;
	unsigned int vlan = 65535, priority = skb->priority & 7;
	bool found = false;

	if (!netif_carrier_ok(dev) || skb->len < ETH_HLEN || skb->len > 1522) goto drop;
	if (!vlan_get_tag(skb, &tci)) { vlan = tci & VLAN_VID_MASK; priority = tci >> VLAN_PRIO_SHIFT; }
	rcu_read_lock();
	state = rcu_dereference(pon->itu_paths);
	path = state ? rtl_itu_path_select(&state->paths, vlan, priority) : NULL;
	if (path && READ_ONCE(pon->itu_paths_ready)) {
		frame.header_high |= (u32)path->index << 18;
		frame.header_low |= path->queue | ((32 + path->tcont) << 3);
		found = true;
	}
	rcu_read_unlock();
	if (!found) goto drop;
	return rtl9617c_ni_transmit(pon->ni_dev, dev, skb, &frame);
drop:
	dev->stats.tx_dropped++;
	dev_kfree_skb_any(skb);
	return NETDEV_TX_OK;
}

void rtl_pon_itu_data_receive(struct rtl9617c_pon *pon, struct napi_struct *napi,
	struct sk_buff *skb, unsigned int source)
{
	struct net_device *dev = rcu_dereference(pon->data_devices[0]);
	struct rtl_itu_path_state *state = rcu_dereference(pon->itu_paths);
	unsigned int i, index = source >> 8;
	unsigned int vlan = RTL_ITU_VLAN_ANY, priority = skb->priority & 7;
	u16 tci = 0;

	if ((source & 255) != 7 || !state || !READ_ONCE(pon->itu_paths_ready) ||
	    !netif_running(dev) || !netif_carrier_ok(dev) || skb->len < ETH_HLEN) goto drop;
	if (!vlan_get_tag(skb, &tci)) { vlan = tci & VLAN_VID_MASK; priority = tci >> VLAN_PRIO_SHIFT; }
	for (i = 0; i < state->paths.count; i++)
		if (state->paths.path[i].index == index &&
		    (state->paths.path[i].downstream_pbits & BIT(priority)) &&
		    rtl_itu_path_matches(&state->paths.path[i], vlan)) break;
	if (i == state->paths.count) goto drop;
	dev->stats.rx_packets++;
	dev->stats.rx_bytes += skb->len;
	skb->protocol = eth_type_trans(skb, dev);
	napi_gro_receive(napi, skb);
	return;
drop:
	if (dev) dev->stats.rx_dropped++;
	dev_kfree_skb_any(skb);
}

void rtl_pon_itu_paths_remove(struct rtl9617c_pon *pon)
{
	struct rtl_itu_path_state *state = rcu_access_pointer(pon->itu_paths);

	RCU_INIT_POINTER(pon->itu_paths, NULL);
	if (state) kfree_rcu(state, rcu);
}
