// SPDX-License-Identifier: GPL-2.0-or-later
/* ITU business configuration follows original ca_gpon_port_{tcont_set,gem_port_add}. */
#include <linux/delay.h>
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/rcupdate.h>
#include <linux/slab.h>
#include <linux/unaligned.h>
#include <linux/rtl9617c-bridge.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-gpon-hw.h"
#include "rtl9617c-xgpon-hw.h"
#include "rtl9617c-pon-itu-puc.h"
#include "rtl9617c-pon-vlan-skb.h"

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
	int ret;

	writel(value, pon->mac + address);
	ret = readl_poll_timeout(pon->mac + address, status, !(status & BIT(31)), 1, 100000);
	if (ret)
		dev_err(pon->dev, "ITU table timeout reg=%04x command=%08x status=%08x\n",
			address, value, status);
	return ret;
}

void rtl_pon_itu_carrier(struct rtl9617c_pon *pon)
{
	struct net_device *dev;

	rcu_read_lock();
	dev = rcu_dereference(pon->data_devices[0]);
	if (dev) {
		if (pon->itu_paths_ready && netif_carrier_ok(pon->omci_device) && !pon->xgpon_restore_pending)
			netif_carrier_on(dev);
		else
			netif_carrier_off(dev);
	}
	rcu_read_unlock();
}

int rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *pon, enum rtl_pon_mode mode)
{
	static const struct rtl_itu_paths empty;
	struct net_device *dev = rcu_access_pointer(pon->data_devices[0]);
	int ret = 0;

	lockdep_assert_held(&pon->lock);
	pon->itu_paths_ready = false;
	pon->itu_path_epoch++;
	if (dev) {
		netif_carrier_off(dev);
		ret = rtl9617c_ni_itu_paths(pon->ni_dev, dev,
			rtl_pon_mode_is_epon(mode) ? NULL : &empty);
		pon->itu_paths_error = ret;
	}
	return ret;
}

static int path_program(struct rtl9617c_pon *pon, const struct rtl_itu_paths *paths)
{
	const struct rtl_dq_io io = {pon, path_read, path_write, path_access};
	u32 queues[8] = {255}, active = 0, alloc[32] = {};
	unsigned int i, index;
	bool gpon = pon->mode == RTL_PON_GPON;
	int ret;

	for (index = 1; index < 32; index++) {
		ret = rtl_itu_puc_schedule(&io, index, 0, (const u8[8]){});
		if (ret) return ret;
		if (gpon) {
			if (!(pon->itu_alloc_valid & BIT(index))) continue;
			ret = rtl_gpon_tcont_set(&io, pon->itu_alloc_ids[index], 0, false, false, true);
			if (ret) return ret;
			pon->itu_alloc_valid &= ~BIT(index);
		}
	}
	if (!gpon) {
		/* All business queues are stopped; one stock burst interval covers them. */
		usleep_range(1000, 1100);
		for (index = 1; index < 32; index++) {
			ret = rtl_xgpon_tcont_release(&io, index);
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
		bool upstream = !path->multicast && path->tcont;
		bool downstream = path->downstream_pbits;

		index = path->index;
		if (upstream && !(active & BIT(path->tcont))) {
			if (gpon) {
				pon->itu_alloc_ids[path->tcont] = path->alloc_id;
				pon->itu_alloc_valid |= BIT(path->tcont);
				ret = rtl_gpon_tcont_set(&io, path->alloc_id, path->tcont, true, true, true);
			} else {
				ret = rtl_xgpon_tcont_set(&io, path->tcont, path->alloc_id, true, true);
			}
			if (ret) return ret;
			ret = rtl_itu_puc_schedule(&io, path->tcont, 255, paths->weights[path->tcont]);
			if (ret) return ret;
			queues[path->tcont / 4] |= 255U << ((path->tcont % 4) * 8);
			active |= BIT(path->tcont);
			alloc[path->tcont] = path->alloc_id | BIT(24);
		}
		if (gpon) {
			/* GTC keeps PLOAM's AES decision in the hardware table. */
			__set_bit(path->gem_id, pon->itu_gpon_gems);
			ret = rtl_gpon_downstream_gem_set(&io, path->gem_id, index, downstream, false, false);
			if (ret) return ret;
			if (upstream) {
				ret = rtl_gpon_upstream_gem_set(&io, index, path->gem_id);
				if (ret) return ret;
			}
		} else {
			rtl_xgpon_downstream_gem_set(&io, index, path->gem_id, downstream, path->multicast);
			ret = rtl_xgpon_upstream_gem_set(&io, index, path->gem_id,
				upstream, path->encrypted, path->multicast);
			if (ret) return ret;
		}
		/* Original GEM add uses its internal index as disabled-policer metadata. */
		writel(((pon->fe_enabled ? 24 : 16) << 3) | (7 << 9) |
			(pon->fe_enabled ? 0 : BIT(15)), pon->mac + 0x9028);
		writel(index << 4, pon->mac + 0x9024);
		ret = path_access(pon, 0x9020, 0xc0000000U | index);
		if (ret) return ret;
	}
	for (i = 0; i < 8; i++) {
		if (!gpon && pon->xgpon_restore_pending) {
			pon->xgpon_puc_saved.valid[i] = queues[i];
			writel(i ? 0 : queues[i] & 255, pon->mac + 0x81bc - i * 4);
		} else {
			writel(queues[i], pon->mac + 0x81bc - i * 4);
		}
	}
	if (!gpon)
		memcpy(pon->xgpon_restore_tcont, alloc, sizeof(alloc));
	return 0;
}

ssize_t rtl_pon_itu_paths_write(struct rtl9617c_pon *pon, const char *buf, size_t count, u64 session)
{
	struct rtl_itu_path_state *state, *old;
	struct net_device *dev;
	unsigned int i, j, entries, profiles, schedules, schedule_mask = 0, unique = 0, cursor;
	int ret = 0;

	if (count < 12 || get_unaligned_le16(buf) != 4 || get_unaligned_le32(buf + 8) != count) return -EINVAL;
	entries = get_unaligned_le16(buf + 2);
	profiles = get_unaligned_le16(buf + 4);
	schedules = get_unaligned_le16(buf + 6);
	cursor = 12 + entries * RTL_ITU_PATH_WIRE_BYTES;
	if (entries > RTL_ITU_PATH_MAX || profiles > entries * RTL_EVTO_STAGES || schedules > 31 || cursor > count) return -EMSGSIZE;
	state = kvzalloc(sizeof(*state) + profiles * sizeof(struct rtl_evto_profile), GFP_KERNEL);
	if (!state) return -ENOMEM;
	state->paths.count = entries;
	state->paths.profile_count = profiles;
	for (i = 0; i < entries; i++) {
		const u8 *record = (const u8 *)buf + 12 + i * RTL_ITU_PATH_WIRE_BYTES;
		struct rtl_itu_path *path = &state->paths.path[i];

		path->alloc_id = get_unaligned_le16(record);
		path->gem_id = get_unaligned_le16(record + 2);
		path->vlan = get_unaligned_le16(record + 4);
		path->pbits = record[6]; path->multicast = record[7];
		path->queue = record[8]; path->encrypted = record[9];
		path->tcont = get_unaligned_le16(record + 10);
		path->downstream_pbits = record[12];
		for (j = 0; j < RTL_EVTO_STAGES; j++) {
			path->vlan_profile[j] = get_unaligned_le16(record + 13 + j * 2);
			if (path->vlan_profile[j] != RTL_EVTO_NONE && path->vlan_profile[j] >= profiles) {
				ret = -EINVAL; goto free;
			}
		}
		path->vlan_exclude = record[23]; path->unmarked_mode = record[24]; path->default_priority = record[25];
		memcpy(path->mapper_dscp, record + 26, 24);
		if (path->unmarked_mode > 2 || path->default_priority > 7) { ret = -EINVAL; goto free; }
		if ((path->vlan < RTL_ITU_VLAN_TAGGED && path->vlan > 4094) || path->multicast > 1 ||
		    path->encrypted > 1 || path->queue > 7 || !(path->pbits | path->downstream_pbits) ||
		    (!path->multicast && ((path->pbits && !path->tcont) || path->tcont > 31 ||
		    (path->tcont ? path->alloc_id > 16383 : path->alloc_id != 65535)))) {
			ret = -EINVAL; goto free;
		}
		path->index = 8 + unique;
		for (j = 0; j < i; j++) {
			const struct rtl_itu_path *previous = &state->paths.path[j];

			/* OLTs can create ANI ports before their VLAN filters. Keep
			 * ambiguous paths; software and offload selectors fail closed. */
			if (!path->multicast && !previous->multicast &&
			    path->tcont == previous->tcont && path->alloc_id != previous->alloc_id) {
				ret = -EINVAL; goto free;
			}
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
	for (i = 0; i < profiles; i++) {
		struct rtl_evto_profile *profile = &state->paths.profile[i];
		if (cursor + 32 > count) { ret = -EMSGSIZE; goto free; }
		profile->entity = get_unaligned_le16(buf + cursor);
		profile->input_tpid = get_unaligned_le16(buf + cursor + 2);
		profile->output_tpid = get_unaligned_le16(buf + cursor + 4);
		profile->downstream = buf[cursor + 6]; profile->count = buf[cursor + 7];
		if (profile->count > RTL_EVTO_ROWS || cursor + 32 + profile->count * 16 > count) {
			ret = -EMSGSIZE; goto free;
		}
		memcpy(profile->dscp, buf + cursor + 8, 24);
		memcpy(profile->row, buf + cursor + 32, profile->count * 16);
		if (!rtl_evto_valid(profile)) { ret = -EINVAL; goto free; }
		cursor += 32 + profile->count * 16;
	}
	if (count - cursor != schedules * RTL_ITU_SCHEDULE_WIRE_BYTES) { ret = -EMSGSIZE; goto free; }
	for (i = 0; i < schedules; i++) {
		const u8 *record = (const u8 *)buf + cursor + i * RTL_ITU_SCHEDULE_WIRE_BYTES;
		unsigned int tcont = get_unaligned_le16(record), used = 0;
		bool strict = false;

		if (!tcont || tcont > 31 || get_unaligned_le16(record + 2) ||
		    (schedule_mask & BIT(tcont))) { ret = -EINVAL; goto free; }
		for (j = 0; j < entries; j++)
			if (state->paths.path[j].tcont == tcont && !state->paths.path[j].multicast)
				used = 1;
		if (!used || !!record[4] != !!record[5]) { ret = -EINVAL; goto free; }
		/* Stock PUC programming keeps WRR below SP and couples queue0/1. */
		for (j = 0; j < 8; j++) {
			if (!record[4 + j]) strict = true;
			else if (strict) { ret = -EINVAL; goto free; }
		}
		memcpy(state->paths.weights[tcont], record + 4, 8);
		schedule_mask |= BIT(tcont);
	}

	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (session != pon->itu_line_epoch) {
		ret = -ESTALE; goto unlock;
	}
	if (pon->gpon_drain.phase || pon->xgpon_recovery_phase) {
		ret = -EBUSY; goto unlock;
	}
	if (rtl_pon_mode_is_epon(pon->mode) || !pon->puc_configured || !pon->pdc_configured) {
		ret = -EOPNOTSUPP; goto unlock;
	}
	if (entries && !netif_carrier_ok(pon->omci_device)) { ret = -EAGAIN; goto unlock; }
	for (i = 0; i < entries; i++)
		if (pon->mode == RTL_PON_GPON && (state->paths.path[i].gem_id > 4095 ||
		    (state->paths.path[i].tcont && state->paths.path[i].alloc_id > 4095))) {
			ret = -EINVAL; goto unlock;
		}
	old = rcu_dereference_protected(pon->itu_paths, lockdep_is_held(&pon->lock));
	if (old) state->paths.generation = old->paths.generation;
	if (pon->itu_paths_ready && old && rtl_itu_paths_bytes(&old->paths) == rtl_itu_paths_bytes(&state->paths) &&
	    !memcmp(&old->paths, &state->paths, rtl_itu_paths_bytes(&state->paths)))
		goto unlock;
	dev = rcu_access_pointer(pon->data_devices[0]);
	netif_tx_disable(dev);
	rtl_pon_itu_paths_invalidate(pon, pon->mode);
	ret = pon->itu_paths_error;
	if (ret) goto wake;
	ret = path_program(pon, &state->paths);
	if (ret) goto wake;
	state->paths.generation = pon->itu_path_epoch;
	ret = rtl9617c_ni_itu_paths(pon->ni_dev, dev, &state->paths);
	if (ret) goto wake;
	old = rcu_replace_pointer(pon->itu_paths, state, lockdep_is_held(&pon->lock));
	if (old) kvfree_rcu(old, rcu);
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
	kvfree(state);
	return ret ? ret : count;
}

/* kernfs splits binary writes at PAGE_SIZE. Commit only the complete snapshot. */
ssize_t rtl_pon_itu_paths_upload(struct rtl9617c_pon *pon, const char *buf, loff_t offset, size_t count)
{
	void *complete = NULL;
	size_t bytes = 0;
	u64 session = 0;
	int ret = 0;
	mutex_lock(&pon->control_lock);
	if (!offset) {
		if (count < 12 || get_unaligned_le16(buf) != 4) { ret = -EINVAL; goto unlock; }
		bytes = get_unaligned_le32(buf + 8);
		if (bytes < 12 || bytes > RTL_ITU_CONFIG_WIRE_MAX) { ret = -EMSGSIZE; goto unlock; }
		kvfree(pon->itu_upload);
		pon->itu_upload = kvmalloc(bytes, GFP_KERNEL);
		pon->itu_upload_received = 0;
		pon->itu_upload_size = bytes;
		if (!pon->itu_upload) { ret = -ENOMEM; goto unlock; }
		pon->itu_upload_epoch = READ_ONCE(pon->itu_line_epoch);
	}
	if (!pon->itu_upload || offset != pon->itu_upload_received ||
	    count > pon->itu_upload_size - pon->itu_upload_received) { ret = -EINVAL; goto unlock; }
	memcpy(pon->itu_upload + offset, buf, count);
	pon->itu_upload_received += count;
	if (pon->itu_upload_received == pon->itu_upload_size) {
		complete = pon->itu_upload; bytes = pon->itu_upload_size;
		session = pon->itu_upload_epoch;
		pon->itu_upload = NULL;
	}
unlock:
	mutex_unlock(&pon->control_lock);
	if (complete) {
		ret = rtl_pon_itu_paths_write(pon, complete, bytes, session);
		kvfree(complete);
	}
	return ret < 0 ? ret : count;
}

ssize_t rtl_pon_itu_paths_state(struct rtl9617c_pon *pon, char *buf)
{
	struct rtl_itu_path_state *state;
	unsigned int i;
	ssize_t length;

	lockdep_assert_held(&pon->lock);
	state = rcu_dereference_protected(pon->itu_paths, lockdep_is_held(&pon->lock));
	length = sysfs_emit(buf, "configured=%u error=%d epoch=%llu paths=%u session=%llu profiles=%u\n",
		pon->itu_paths_ready, pon->itu_paths_error, pon->itu_path_epoch,
		state ? state->paths.count : 0, pon->itu_line_epoch, state ? state->paths.profile_count : 0);
	if (state) for (i = 0; i < state->paths.count && length < PAGE_SIZE - 180; i++) {
		const struct rtl_itu_path *path = &state->paths.path[i];

		length += sysfs_emit_at(buf, length, "gem=%u index=%u alloc=%u tcont=%u queue=%u vlan=%u pbits=%02x multicast=%u upstream_aes=%u downstream_pbits=%02x vlan_exclude=%02x unmarked=%u\n",
			path->gem_id, path->index, path->alloc_id, path->tcont, path->queue,
			path->vlan, path->pbits, path->multicast, path->encrypted, path->downstream_pbits,
			path->vlan_exclude, path->unmarked_mode);
	}
	return length;
}

static bool rtl_pon_itu_service_entry(struct net_device *dev, const struct rtl_evto_packet *packet)
{
	if (!packet->count || netif_is_bridge_port(dev)) return false;
	for (unsigned int i = 0; i < packet->count; i++) {
		dev = __vlan_find_dev_deep_rcu(dev, htons(packet->tag[i].tpid), packet->tag[i].tci & 4095);
		/* A VLAN bridge member still selects an explicit local service. */
		if (!dev) return false;
	}
	return true;
}

netdev_tx_t rtl_pon_itu_data_transmit(struct rtl9617c_pon *pon, struct sk_buff *skb, struct net_device *dev)
{
	struct rtl_itu_path_state *state;
	const struct rtl_itu_path *path;
	struct rtl9617c_ni_frame frame = {.header_high = BIT(30), .header_low = (16 << 9) | BIT(29)};
	struct rtl_evto_packet input, output;
	struct sk_buff *wire = skb;
	unsigned int wire_tags;
	netdev_tx_t ret;
	bool found = false;

	if (!netif_carrier_ok(dev)) goto drop;
	rcu_read_lock();
	state = rcu_dereference(pon->itu_paths);
	if (!state || !READ_ONCE(pon->itu_paths_ready) || rtl_evto_skb_read(&state->paths, skb, &input, &wire_tags))
		goto unlock;
	input.service_entry = rtl_pon_itu_service_entry(dev, &input);
	path = rtl_itu_path_transmit_vlan(&state->paths, &input, &output);
	if (!path) goto unlock;
	/* DMA BUSY leaves the original packet queued for a fresh GEM lookup. */
	if (skb_vlan_tag_present(skb) || input.count != output.count ||
	    skb->priority != output.priority ||
	    memcmp(input.tag, output.tag, input.count * sizeof(input.tag[0]))) {
		wire = skb_clone(skb, GFP_ATOMIC);
		if (!wire) goto unlock;
	}
	/* A NI BID holds2048 bytes, including32 bytes prefix and16 bytes HEADER_A. */
	if (!rtl_evto_skb_write(wire, &input, &output, wire_tags) && wire->len <= 2000) {
		frame.header_high |= (u32)path->index << 18;
		frame.header_low |= path->queue | ((32 + path->tcont) << 3);
		found = true;
	}
unlock:
	rcu_read_unlock();
	if (!found) {
		if (wire && wire != skb) dev_kfree_skb_any(wire);
		goto drop;
	}
	ret = rtl9617c_ni_transmit(pon->ni_dev, dev, wire, &frame);
	if (wire != skb) {
		if (ret == NETDEV_TX_BUSY) dev_kfree_skb_any(wire);
		else dev_consume_skb_any(skb);
	}
	return ret;
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
	const struct rtl_itu_path *path;
	unsigned int index = source >> 8, wire_tags;
	struct rtl_evto_packet input, output;

	if ((source & 255) != 7 || !state || !READ_ONCE(pon->itu_paths_ready) ||
	    !netif_running(dev) || !netif_carrier_ok(dev) || skb->len < ETH_HLEN) goto drop;
	if (rtl_evto_skb_read(&state->paths, skb, &input, &wire_tags)) goto drop;
	input.service_entry = 1;
	path = rtl_itu_path_receive_vlan(&state->paths, index, &input, &output);
	if (!path || !rtl_pon_itu_service_entry(dev, &output)) {
		input.service_entry = 0;
		path = rtl_itu_path_receive_vlan(&state->paths, index, &input, &output);
	}
	if (!path || rtl_evto_skb_write(skb, &input, &output, wire_tags)) goto drop;
	{
		struct rtl9617c_bridge_frame *wire = skb_ext_add(skb, SKB_EXT_RTL9617C_BRIDGE);
		if (wire) {
			memset(wire, 0, sizeof(*wire));
			wire->pon_gem = index; wire->pon_generation = state->paths.generation;
			wire->tags = input.count;
			for (unsigned int i = 0; i < input.count && i < 2; i++)
				wire->tag[i] = (struct rtl9617c_bridge_tag){input.tag[i].tci, input.tag[i].tpid};
		}
	}
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
	if (state) kvfree_rcu(state, rcu);
	kvfree(pon->itu_upload);
}
