// SPDX-License-Identifier: GPL-2.0-only
/* Native RXQ/RPQ ownership and packet dispatch,following the board's
 * hal_check_rxrdy/hal_handle_rxbd_info/update_rxbd/notify_rxdone path. */
#include <linux/dma-mapping.h>
#include <linux/crc32.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
#include <linux/unaligned.h>
#include <net/mac80211.h>
#include "receive.h"
#include "rx.h"

#define RTL_AX_RX_IRQS (BIT(20) | BIT(19) | BIT(2) | BIT(0))
#define RTL_AX_IRQ_INDICATION (BIT(29) | BIT(24))
#define RTL_AX_RX_SPARES 2

static bool rx_packet_trace;
module_param(rx_packet_trace, bool, 0644);
MODULE_PARM_DESC(rx_packet_trace, "Capture RX packet prefixes and software FCS for diagnosis");

struct rtl_ax_data_observation {
	u64 sequence;
	u32 words[8], bytes, info_bytes, payload_offset;
	u32 computed_fcs, frame_fcs;
	u8 prefix[64], prefix_bytes;
};

struct rtl_ax_rx_queue {
	u8 *descriptors, *buffers;
	dma_addr_t descriptor_dma, buffer_dma;
	unsigned int consumer, tag, buffer_bytes;
	u64 packets, malformed, tag_pending, unhandled;
	u64 types[16], beacons, wifi_crc_ok, wifi_crc_error;
	u64 wifi_icv_error, wifi_data_icv_crc_ok;
	u64 wifi_frames[4][16], wifi_encoding[4], wifi_data_encoding[4];
	u64 data_fcs_ok[4], data_fcs_error[4];
	struct rtl_ax_data_observation last_data[4];
};

static u32 rtl_ax_rx_buffer_offset(struct rtl_ax_rx_queue *queue, unsigned int slot)
{
	return get_unaligned_le32(queue->descriptors + slot * 8 + 4) - (u32)queue->buffer_dma;
}

struct rtl_ax_shortcut_seen { u32 frames, bytes, msdus; };

struct rtl_ax_rx {
	struct pci_dev *pdev;
	struct device *rpq_device;
	struct device *pe_device;
	void *rpq_memory, *pe_forward;
	dma_addr_t pe_forward_dma;
	bool rpq_owned, rpq_transfer, buffer_pool_initialized;
	u32 irq_mask;
	const struct rtl_ax_io *io;
	struct rtl_ax_rx_queue queues[2];
	rtl_ax_rx_handler handler;
	void *handler_ctx;
	struct delayed_work work;
	spinlock_t irq_lock;
	bool running, irq_requested, vectors;
	int irq;
	u64 interrupts, unavailable;
	u64 pe_rx_delivered, pe_rx_malformed;
	u64 pe_rx_data, pe_rx_data_bytes, pe_rx_pm_data;
	struct ieee80211_key_conf *keys[128];
	u8 uplink_trigger_tids[128];
	u16 uplink_pending_tids[128];
	struct mutex uplink_lock;
	struct ieee80211_hw *hw;
	unsigned int uplink_wifi_id;
	void *uplink_states;
	dma_addr_t uplink_states_dma;
	struct rtl_ax_shortcut_seen uplink_seen[128][16];
	u32 halt_error;
};

void rtl_ax_rx_uapsd(struct rtl_ax_rx *rx, unsigned int macid, u8 queues)
{
	static const u8 queue_mask[8] = {8, 4, 4, 8, 2, 2, 1, 1};
	u8 tids = 0;
	u32 value;

	/* WMM AC flags use VO/VI/BK/BE order; the PE consumes a TID bitmap. */
	for (unsigned int tid = 0; tid < 8; tid++)
		if (queues & queue_mask[tid]) tids |= BIT(tid);
	mutex_lock(&rx->uplink_lock);
	rx->uplink_trigger_tids[macid] = tids;
	value = get_unaligned_le32(rx->uplink_states + 0x10000 + macid * 4);
	put_unaligned_le32((value & ~GENMASK(23, 16)) | (u32)tids << 16,
		rx->uplink_states + 0x10000 + macid * 4);
	dma_wmb();
	mutex_unlock(&rx->uplink_lock);
}

void rtl_ax_rx_key(struct rtl_ax_rx *rx, unsigned int macid, struct ieee80211_key_conf *key)
{
	u8 *states = rx->uplink_states;
	mutex_lock(&rx->uplink_lock);
	put_unaligned_le32(0, states + 0x10000 + macid * 4);
	dma_wmb();
	rx->keys[macid] = key;
	rx->uplink_pending_tids[macid] = 0;
	if (!key) goto unlock;
	memset(states + macid * 512, 0, 512);
	memset(rx->uplink_seen[macid], 0, sizeof(rx->uplink_seen[macid]));
unlock:
	mutex_unlock(&rx->uplink_lock);
}

bool rtl_ax_rx_uplink_active(struct rtl_ax_rx *rx, unsigned int macid)
{
	return rx->uplink_wifi_id &&
		(get_unaligned_le32(rx->uplink_states + 0x10000 + macid * 4) & BIT(31));
}

void rtl_ax_rx_uplink_enable(struct rtl_ax_rx *rx, unsigned int macid)
{
	mutex_lock(&rx->uplink_lock);
	if (rx->keys[macid] && rx->uplink_wifi_id) {
		dma_wmb();
		put_unaligned_le32(BIT(31) | rx->uplink_wifi_id << 8 | rx->keys[macid]->keyidx |
			(u32)rx->uplink_trigger_tids[macid] << 16,
			rx->uplink_states + 0x10000 + macid * 4);
	}
	mutex_unlock(&rx->uplink_lock);
}

void rtl_ax_rx_uplink_disable(struct rtl_ax_rx *rx, unsigned int macid)
{
	mutex_lock(&rx->uplink_lock);
	put_unaligned_le32(0, rx->uplink_states + 0x10000 + macid * 4);
	dma_wmb();
	mutex_unlock(&rx->uplink_lock);
}

void rtl_ax_rx_uplink_peer(struct rtl_ax_rx *rx, unsigned int wifi_id, struct ieee80211_hw *hw)
{
	unsigned int macid;
	rx->uplink_wifi_id = wifi_id;
	rx->hw = hw;
	for (macid = 1; macid < 128; macid++) {
		struct ieee80211_key_conf *key = rx->keys[macid];
		if (key && get_unaligned_le32(rx->rpq_memory + 0x11a00 + macid * 4))
			put_unaligned_le32(BIT(31) | wifi_id << 8 | key->keyidx |
			(u32)rx->uplink_trigger_tids[macid] << 16,
			rx->uplink_states + 0x10000 + macid * 4);
	}
	dma_wmb();
	put_unaligned_le32(rx->uplink_states_dma, rx->rpq_memory + 0x21a8);
}

static bool rtl_ax_rx_uplink_publish(struct rtl_ax_rx *rx, struct ieee80211_sta *sta,
	struct ieee80211_key_conf *key, unsigned int macid, unsigned int tid)
{
	u8 *state = rx->uplink_states + (macid * 16 + tid) * 32;
	u16 next;
	u8 pn[6];

	if (!ieee80211_rx_shortcut_snapshot(sta, key, tid, &next, pn)) return false;
	put_unaligned_le32((u32)pn[2] << 24 | (u32)pn[3] << 16 | (u32)pn[4] << 8 | pn[5], state + 4);
	put_unaligned_le32((u32)pn[0] << 8 | pn[1], state + 8);
	dma_wmb();
	put_unaligned_le32(BIT(31) | next, state);
	rx->uplink_pending_tids[macid] &= ~BIT(tid);
	return true;
}

static void rtl_ax_rx_uplink_sync(struct rtl_ax_rx *rx)
{
	u8 *memory = rx->rpq_memory;
	unsigned int macid, tid;

	if (!rx->uplink_wifi_id) return;
	mutex_lock(&rx->uplink_lock);
	local_bh_disable(); rcu_read_lock();
	for (macid = 1; macid < 128; macid++) {
		struct ieee80211_key_conf *key = rx->keys[macid];
		struct ieee80211_sta *sta;
		if (!key) continue;
		sta = ieee80211_find_sta_by_ifaddr(rx->hw, memory + 0xe400 + macid * 8, NULL);
		for (tid = 0; sta && tid < 16; tid++) {
			u8 *state = rx->uplink_states + (macid * 16 + tid) * 32;
			struct rtl_ax_shortcut_seen *seen = &rx->uplink_seen[macid][tid];
			u32 guard, snapshot[6], count;
			u8 pn[6];
			do {
				guard = get_unaligned_le32(state + 24);
				dma_rmb();
				for (unsigned int i = 0; i < 6; i++) snapshot[i] = get_unaligned_le32(state + i * 4);
				dma_rmb();
			} while ((guard & 1) || guard != get_unaligned_le32(state + 24));
			count = snapshot[3] - seen->frames;
			if (!count) goto resume;
			pn[0] = snapshot[2] >> 8; pn[1] = snapshot[2]; pn[2] = snapshot[1] >> 24;
			pn[3] = snapshot[1] >> 16; pn[4] = snapshot[1] >> 8; pn[5] = snapshot[1];
			ieee80211_rx_shortcut_sync(sta, key, tid, snapshot[0] & 4095, pn, min_t(u32, count, 2048));
			ieee80211_rx_shortcut_account(sta, tid, count, snapshot[5] - seen->msdus, snapshot[4] - seen->bytes);
			seen->frames = snapshot[3]; seen->bytes = snapshot[4]; seen->msdus = snapshot[5];
resume:
			/* A reorder timeout can finish after the last DMA frame callback. */
			if ((rx->uplink_pending_tids[macid] & BIT(tid)) &&
			    (get_unaligned_le32(rx->uplink_states + 0x10000 + macid * 4) & BIT(31)))
				rtl_ax_rx_uplink_publish(rx, sta, key, macid, tid);
		}
	}
	rcu_read_unlock(); local_bh_enable();
	mutex_unlock(&rx->uplink_lock);
}

void rtl_ax_rx_uplink_retire(struct rtl_ax_rx *rx, unsigned int macid)
{
	/* The caller has paused PE. Publish its final PN/SN before key removal. */
	put_unaligned_le32(0, rx->uplink_states + 0x10000 + macid * 4);
	dma_wmb();
	rtl_ax_rx_uplink_sync(rx);
}

static void rtl_ax_rx_uplink_seed(struct rtl_ax_rx *rx, const struct rtl_ax_rx_packet *packet)
{
	u8 *memory = rx->rpq_memory;
	const u8 *frame = packet->payload;
	struct ieee80211_sta *sta;
	unsigned int macid, tid;
	u16 fc;

	if (!rx->uplink_wifi_id || packet->type || packet->bytes < 54) return;
	fc = get_unaligned_le16(frame);
	if ((fc & 0x47ff) != 0x4188 || frame[22] & 15) return;
	mutex_lock(&rx->uplink_lock);
	for (macid = 1; macid < 128; macid++)
		if (rx->keys[macid] && !memcmp(frame + 10, memory + 0xe400 + macid * 8, 6)) break;
	if (macid == 128) goto unlock;
	tid = frame[24] & 15;
	rx->uplink_pending_tids[macid] |= BIT(tid);
	local_bh_disable(); rcu_read_lock();
	sta = ieee80211_find_sta_by_ifaddr(rx->hw, frame + 10, NULL);
	if (!sta || !rtl_ax_rx_uplink_publish(rx, sta, rx->keys[macid], macid, tid))
		put_unaligned_le32(0, rx->uplink_states + (macid * 16 + tid) * 32);
	rcu_read_unlock(); local_bh_enable();
unlock:
	mutex_unlock(&rx->uplink_lock);
}

static bool rtl_ax_rx_drain(struct rtl_ax_rx *rx, unsigned int number)
{
	struct rtl_ax_rx_queue *queue = &rx->queues[number];
	const struct rtl_ax_io *io = rx->io;
	unsigned int count, index = 0x1218 + number * 4;
	unsigned int hardware = (io->read32(io->ctx, index) >> 16) & 0x3ff;

	for (count = 0; count < 128; count++) {
		struct rtl_ax_rx_packet packet = {};
		unsigned int expected = rtl_ax_rx_next_tag(queue->tag), attempt, info;
		u8 *buffer;
		int ret;

		if (hardware == queue->consumer) {
			hardware = (io->read32(io->ctx, index) >> 16) & 0x3ff;
			if (hardware == queue->consumer)
				return false;
		}
		if (hardware >= RTL_AX_RX_ENTRIES) {
			queue->malformed++;
			return false;
		}
		buffer = queue->buffers + rtl_ax_rx_buffer_offset(queue, queue->consumer);
		/* The actual board function polls10000 coherent tag observations,
		 * wraps8191→1 and changes its saved tag only after a match. */
		for (attempt = 0; attempt < 10000; attempt++) {
			info = le32_to_cpu(READ_ONCE(*(__le32 *)buffer));
			if (((info >> 16) & 8191) == expected)
				break;
			cpu_relax();
		}
		if (attempt == 10000) {
			queue->tag_pending++;
			return true;
		}
		dma_rmb();
		ret = rtl_ax_receive_packet(buffer, queue->buffer_bytes, &packet);
		queue->tag = expected;
		if (ret)
			queue->malformed++;
		else {
			queue->packets++;
			queue->types[packet.type]++;
			if (packet.type == 0) {
				queue->wifi_crc_ok += !packet.crc_error;
				queue->wifi_crc_error += packet.crc_error;
				queue->wifi_icv_error += packet.icv_error;
			}
			if (packet.type == 0 && !packet.crc_error && packet.bytes >= 2) {
				u16 fc = packet.payload[0] | (u16)packet.payload[1] << 8;
				unsigned int type = (fc >> 2) & 3, subtype = (fc >> 4) & 15;
				/* Original RX WD word1 rate16:24: legacy/HT/VHT/HE.
				 * Count before the Linux receive callback, so a missing
				 * userspace Data frame can be located at the host boundary. */
				unsigned int encoding = (rtl_ax_ap_read32(packet.descriptor + 4) >> 23) & 3;

				queue->wifi_frames[type][subtype]++;
				queue->wifi_encoding[encoding]++;
				if (type == 2 && !(subtype & 4)) {
					struct rtl_ax_data_observation *last = &queue->last_data[encoding];
					unsigned long flags;
					u32 computed = 0, frame = 0;

					queue->wifi_data_encoding[encoding]++;
					queue->wifi_data_icv_crc_ok += packet.icv_error;
					if (!READ_ONCE(rx_packet_trace))
						goto data_observed;
					if (packet.bytes >= 4) {
						computed = crc32_le(~0U, packet.payload, packet.bytes - 4) ^ ~0U;
						frame = rtl_ax_ap_read32(packet.payload + packet.bytes - 4);
						queue->data_fcs_ok[encoding] += computed == frame;
						queue->data_fcs_error[encoding] += computed != frame;
					}
					/* Snapshot the actual DMA result before Linux submission.
					 * The short copy shares the IRQ lock with its sysfs reader. */
					spin_lock_irqsave(&rx->irq_lock, flags);
					last->sequence = queue->wifi_data_encoding[encoding];
					memset(last->words, 0, sizeof(last->words));
					memcpy(last->words, packet.descriptor, packet.long_descriptor ? 32 : 16);
					last->bytes = packet.bytes;
					last->info_bytes = packet.driver_info_bytes;
					last->payload_offset = packet.payload - packet.descriptor;
					last->computed_fcs = computed;
					last->frame_fcs = frame;
					last->prefix_bytes = min_t(unsigned int, packet.bytes, sizeof(last->prefix));
					memcpy(last->prefix, packet.payload, last->prefix_bytes);
					spin_unlock_irqrestore(&rx->irq_lock, flags);
				}

data_observed:
				if ((fc & 0x00ff) == 0x0080 && packet.bytes >= 36)
					queue->beacons++;
			}
			ret = rx->handler(rx->handler_ctx, &packet);
			if (ret == -EOPNOTSUPP)
				queue->unhandled++;
			else if (ret)
				queue->malformed++;
		}
		/* Return the same native coherent buffer after synchronous dispatch;
		 * no original skb-pointer/headroom layout belongs in this allocator. */
		WRITE_ONCE(*(__le32 *)buffer, 0);
		rtl_ax_rxbd(queue->descriptors + queue->consumer * 8,
			    lower_32_bits(queue->buffer_dma + (buffer - queue->buffers)),
			    queue->buffer_bytes);
		dma_wmb();
		queue->consumer = (queue->consumer + 1) % RTL_AX_RX_ENTRIES;
		io->write16(io->ctx, index, queue->consumer);
	}
	return true;
}

static void rtl_ax_rx_pe_drain(struct rtl_ax_rx *rx)
{
	u8 *mailbox = rx->rpq_memory + 0x2000;
	u32 consumer = get_unaligned_le32(mailbox + 296);
	u32 budget = 64;

	while (budget-- && consumer != get_unaligned_le32(mailbox + 292)) {
		struct rtl_ax_rx_packet packet = {.pe_completed = true};
		dma_rmb();
		if (!rtl_ax_receive_packet(rx->pe_forward + consumer * 512, 512, &packet))
			rx->handler(rx->handler_ctx, &packet);
		consumer = (consumer + 1) & 255;
		dma_wmb();
		put_unaligned_le32(consumer, mailbox + 296);
	}
}

static void rtl_ax_rx_pe_rxq_drain(struct rtl_ax_rx *rx)
{
	u8 *mailbox = rx->rpq_memory + 0x2000;
	struct rtl_ax_rx_queue *queue = &rx->queues[0];
	u32 consumer = get_unaligned_le32(mailbox + 380);
	u32 budget = 64;

	while (budget-- && consumer != get_unaligned_le32(mailbox + 376)) {
		struct rtl_ax_rx_packet packet = {};
		u32 slot;
		dma_addr_t dma;
		u8 *buffer;
		int ret;

		dma_rmb();
		slot = get_unaligned_le32(rx->rpq_memory + 0xf400 + consumer * 4);
		u32 offset = rtl_ax_rx_buffer_offset(queue, slot);
		dma = queue->buffer_dma + offset;
		buffer = queue->buffers + offset;
		rtl_ax_rx_uplink_sync(rx);
		dma_sync_single_for_cpu(rx->pe_device, dma, queue->buffer_bytes, DMA_BIDIRECTIONAL);
		ret = rtl_ax_receive_packet(buffer, queue->buffer_bytes, &packet);
		if (!ret) {
			if (!packet.type && packet.bytes >= 54 &&
			    (get_unaligned_le16(packet.payload) & 0x47ff) == 0x4188) {
				rx->pe_rx_data++;
				rx->pe_rx_data_bytes += packet.bytes;
				rx->pe_rx_pm_data += !!(packet.payload[1] & 0x10);
			}
			rx->handler(rx->handler_ctx, &packet);
			rtl_ax_rx_uplink_seed(rx, &packet);
			rx->pe_rx_delivered++;
		} else rx->pe_rx_malformed++;
		/* Drop ARM cache aliases before PE/PCIe reuse this RX buffer. */
		dma_sync_single_for_device(rx->pe_device, dma, queue->buffer_bytes, DMA_BIDIRECTIONAL);
		consumer = (consumer + 1) & 255;
		dma_wmb();
		put_unaligned_le32(BIT(30), rx->rpq_memory + 0xf000 + slot * 4);
		put_unaligned_le32(consumer, mailbox + 380);
	}
}

void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx)
{
	if (READ_ONCE(rx->running)) mod_delayed_work(system_wq, &rx->work, 0);
}

void rtl_ax_rx_pe_rpq_start(struct rtl_ax_rx *rx, void *memory)
{
	u8 *mailbox = memory + 0x2000;
	bool used[RTL_AX_RX_ENTRIES + RTL_AX_RX_SPARES] = {};
	unsigned int i, spare = 0;
	unsigned long flags;

	/* Stop both physical host consumers before publishing their indices to PE. */
	spin_lock_irqsave(&rx->irq_lock, flags);
	rx->rpq_transfer = true;
	rx->rpq_owned = true;
	rx->irq_mask = 0;
	rx->io->write32(rx->io->ctx, 0x10b0, rx->irq_mask);
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	synchronize_irq(rx->irq);
	cancel_delayed_work_sync(&rx->work);
	/* Transfer the current consumer/tag, including any not-yet-ready DMA.
	 * The PE run loop waits for tags; enabling offload need not drain them. */
	dma_sync_single_for_device(rx->pe_device, rx->queues[1].buffer_dma, SZ_128K, DMA_BIDIRECTIONAL);
	dma_sync_single_for_device(rx->pe_device, rx->queues[0].buffer_dma,
		(RTL_AX_RX_ENTRIES + RTL_AX_RX_SPARES) * RTL_AX_RXQ_BUFFER_BYTES, DMA_BIDIRECTIONAL);
	rx->rpq_memory = memory;
	for (i = 0; i < RTL_AX_RX_ENTRIES; i++) {
		u32 id = rtl_ax_rx_buffer_offset(&rx->queues[0], i) / RTL_AX_RXQ_BUFFER_BYTES;
		put_unaligned_le32(id, memory + 0x17400 + i * 4);
		used[id] = true;
	}
	/* Retained NI tail buffers stay outside the free pool across handoff. */
	if (!rx->buffer_pool_initialized) {
		memset(memory + 0x17800, 0, 0x20);
		for (i = 0; i < ARRAY_SIZE(used); i++)
			if (!used[i]) put_unaligned_le32(i + 1, memory + 0x17800 + spare++ * 4);
		rx->buffer_pool_initialized = true;
	}
	put_unaligned_le32(rx->queues[0].descriptor_dma, mailbox + 134 * 4);
	memset(memory + 0xf000, 0, 0x800);
	memset(rx->uplink_states, 0, SZ_128K);
	memset(rx->uplink_seen, 0, sizeof(rx->uplink_seen));
	put_unaligned_le32(0, mailbox + 376);
	put_unaligned_le32(0, mailbox + 380);
	put_unaligned_le32(rx->queues[1].consumer, mailbox + 280);
	put_unaligned_le32(rx->queues[1].tag, mailbox + 284);
	put_unaligned_le32(rx->pe_forward_dma, mailbox + 288);
	put_unaligned_le32(0, mailbox + 292);
	put_unaligned_le32(0, mailbox + 296);
	put_unaligned_le32(rx->queues[0].consumer, mailbox + 324);
	put_unaligned_le32(rx->queues[0].tag, mailbox + 328);
	put_unaligned_le32(rx->queues[0].consumer, mailbox + 332);
	put_unaligned_le32(rx->queues[0].consumer, mailbox + 336);
	dma_wmb();
	put_unaligned_le32(rx->queues[1].buffer_dma, mailbox + 272);
	put_unaligned_le32(rx->queues[0].buffer_dma, mailbox + 320);
	WRITE_ONCE(rx->rpq_transfer, false);
	rx->io->write32(rx->io->ctx, 0x30b0, RTL_AX_IRQ_INDICATION);
	rtl_ax_rx_pe_wake(rx);
}

static void rtl_ax_rx_tail_recycle(struct rtl_ax_rx *rx)
{
	u8 *memory = rx->rpq_memory;
	u32 read = get_unaligned_le32(memory + 0x216c);
	for (unsigned int i = 0; i < 2; i++) {
		u8 *held = memory + 0x17810 + i * 8;
		u32 id = get_unaligned_le32(held);
		if (!id || (s32)((read - get_unaligned_le32(held + 4) - 2) << 1) < 0) continue;
		u32 offset = (id - 1) * RTL_AX_RXQ_BUFFER_BYTES;
		WRITE_ONCE(*(__le32 *)(rx->queues[0].buffers + offset), 0);
		dma_sync_single_for_device(rx->pe_device, rx->queues[0].buffer_dma + offset,
			RTL_AX_RXQ_BUFFER_BYTES, DMA_BIDIRECTIONAL);
		put_unaligned_le32(id, memory + 0x17800 + (get_unaligned_le32(memory + 0x17800) ? 4 : 0));
		put_unaligned_le32(0, held);
	}
}

static int rtl_ax_rx_tail_hold(struct rtl_ax_rx *rx, u32 slot, u32 end)
{
	u8 *memory = rx->rpq_memory;
	u32 available = get_unaligned_le32(memory + 0x17800) ? 0 : 1;
	u32 replacement = get_unaligned_le32(memory + 0x17800 + available * 4);
	u32 index = get_unaligned_le32(memory + 0x17810) ? 1 : 0;
	u32 id = rtl_ax_rx_buffer_offset(&rx->queues[0], slot) / RTL_AX_RXQ_BUFFER_BYTES;
	if (!replacement) return -EBUSY;
	put_unaligned_le32(id + 1, memory + 0x17810 + index * 8);
	put_unaligned_le32(end, memory + 0x17814 + index * 8);
	put_unaligned_le32(0, memory + 0x17800 + available * 4);
	put_unaligned_le32(replacement - 1, memory + 0x17400 + slot * 4);
	put_unaligned_le32(rx->queues[0].buffer_dma + (replacement - 1) * RTL_AX_RXQ_BUFFER_BYTES,
		rx->queues[0].descriptors + slot * 8 + 4);
	return 0;
}

int rtl_ax_rx_pe_rpq_stop(struct rtl_ax_rx *rx)
{
	u8 *mailbox;
	unsigned long flags;

	if (!rx->rpq_memory) return 0;
	/* Caller has paused PE, so consumer/tag can transfer back to the host. */
	WRITE_ONCE(rx->rpq_transfer, true);
	cancel_delayed_work_sync(&rx->work);
	mailbox = rx->rpq_memory + 0x2000;
	/* PE is paused; these producers cannot advance during the handoff. */
	do { rtl_ax_rx_pe_drain(rx); }
	while (get_unaligned_le32(mailbox + 296) != get_unaligned_le32(mailbox + 292));
	do { rtl_ax_rx_pe_rxq_drain(rx); }
	while (get_unaligned_le32(mailbox + 380) != get_unaligned_le32(mailbox + 376));
	rtl_ax_rx_uplink_sync(rx);
	/* Check NI consumption before returning any outstanding RXBD. */
	{
		u32 slot = get_unaligned_le32(mailbox + 332);
		u32 consumed = get_unaligned_le32(mailbox + 324);
		u32 read = get_unaligned_le32(mailbox + 364);
		for (; slot != consumed; slot = (slot + 1) & 255) {
			u32 owner = get_unaligned_le32(rx->rpq_memory + 0xf000 + slot * 4);
			if (owner & BIT(31)) {
				if ((s32)((read - (owner & 0x7fffffff)) << 1) < 0) return -EBUSY;
			} else if (!(owner & BIT(30))) return -EBUSY;
		}
	}
	rtl_ax_rx_tail_recycle(rx);
	/* Return the RXBD, retaining any NI tail payload behind a replacement. */
	{
		u32 returned = get_unaligned_le32(mailbox + 332);
		u32 consumed = get_unaligned_le32(mailbox + 324);
		while (returned != consumed) {
			u32 owner = get_unaligned_le32(rx->rpq_memory + 0xf000 + returned * 4);
			u32 offset = rtl_ax_rx_buffer_offset(&rx->queues[0], returned);
			u8 *buffer = rx->queues[0].buffers + offset;
			if ((owner & BIT(31)) &&
			    (s32)((get_unaligned_le32(mailbox + 364) - (owner & 0x7fffffff) - 2) << 1) < 0) {
				int ret = rtl_ax_rx_tail_hold(rx, returned, owner & 0x7fffffff);
				if (ret) return ret;
			} else {
				WRITE_ONCE(*(__le32 *)buffer, 0);
				dma_sync_single_for_device(rx->pe_device,
					rx->queues[0].buffer_dma + offset,
					RTL_AX_RXQ_BUFFER_BYTES, DMA_BIDIRECTIONAL);
			}
			put_unaligned_le32(0, rx->rpq_memory + 0xf000 + returned * 4);
			returned = (returned + 1) & 255;
			put_unaligned_le32(returned, mailbox + 332);
		}
		rx->queues[0].consumer = consumed;
		rx->queues[0].tag = get_unaligned_le32(mailbox + 328);
		dma_wmb();
		rx->io->write16(rx->io->ctx, 0x1218, consumed);
	}
	dma_sync_single_for_cpu(rx->pe_device, rx->queues[1].buffer_dma, SZ_128K, DMA_BIDIRECTIONAL);
	rx->queues[1].consumer = get_unaligned_le32(mailbox + 280);
	rx->queues[1].tag = get_unaligned_le32(mailbox + 284);
	put_unaligned_le32(0, mailbox + 272);
	put_unaligned_le32(0, mailbox + 320);
	dma_wmb();
	rx->rpq_memory = NULL;
	rx->uplink_wifi_id = 0;
	put_unaligned_le32(0, mailbox + 424);
	spin_lock_irqsave(&rx->irq_lock, flags);
	rx->rpq_owned = false;
	rx->rpq_transfer = false;
	rx->irq_mask = RTL_AX_RX_IRQS;
	if (rx->running) {
		rx->io->write32(rx->io->ctx, 0x10b0, rx->irq_mask);
		rx->io->write32(rx->io->ctx, 0x30b0, RTL_AX_IRQ_INDICATION);
	}
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	rtl_ax_rx_pe_wake(rx);
	return 0;
}

static void rtl_ax_rx_work(struct work_struct *work)
{
	struct rtl_ax_rx *rx = container_of(to_delayed_work(work), struct rtl_ax_rx, work);
	unsigned long flags;
	bool retry;

	if (!READ_ONCE(rx->running))
		return;
	retry = false;
	if (!READ_ONCE(rx->rpq_transfer)) {
		if (!READ_ONCE(rx->rpq_owned)) {
			retry = rtl_ax_rx_drain(rx, 0);
			retry |= rtl_ax_rx_drain(rx, 1);
		} else if (rx->rpq_memory) {
			rtl_ax_rx_pe_drain(rx);
			rtl_ax_rx_pe_rxq_drain(rx);
			rtl_ax_rx_uplink_sync(rx);
			retry = get_unaligned_le32(rx->rpq_memory + 0x2124) != get_unaligned_le32(rx->rpq_memory + 0x2128) ||
				get_unaligned_le32(rx->rpq_memory + 0x2178) != get_unaligned_le32(rx->rpq_memory + 0x217c);
		}
	}
	spin_lock_irqsave(&rx->irq_lock, flags);
	if (rx->running) {
		if (retry)
			/* A tick of exception backlog forces subsequent PE data onto ARM. */
			mod_delayed_work(system_wq, &rx->work,
				rx->rpq_owned ? 0 : msecs_to_jiffies(1));
		else
			rx->io->write32(rx->io->ctx, 0x30b0, RTL_AX_IRQ_INDICATION);
	}
	spin_unlock_irqrestore(&rx->irq_lock, flags);
}

static irqreturn_t rtl_ax_rx_interrupt(int irq, void *context)
{
	struct rtl_ax_rx *rx = context;
	const struct rtl_ax_io *io = rx->io;
	unsigned long flags;
	unsigned int indication, pending, halt;

	spin_lock_irqsave(&rx->irq_lock, flags);
	if (!rx->running) {
		spin_unlock_irqrestore(&rx->irq_lock, flags);
		return IRQ_NONE;
	}
	indication = io->read32(io->ctx, 0x30b4) & RTL_AX_IRQ_INDICATION;
	pending = indication & BIT(29) ? io->read32(io->ctx, 0x10b4) & rx->irq_mask : 0;
	halt = indication & BIT(24) ? io->read32(io->ctx, 0x01a4) & BIT(21) : 0;
	if (!pending && !halt) {
		spin_unlock_irqrestore(&rx->irq_lock, flags);
		return IRQ_NONE;
	}
	io->write32(io->ctx, 0x30b0, 0);
	if (pending)
		io->write32(io->ctx, 0x10b4, pending);
	if (halt) {
		rx->halt_error = io->read32(io->ctx, 0x016c);
		io->write32(io->ctx, 0x01a4, halt);
	}
	if (pending & (BIT(20) | BIT(19)))
		rx->unavailable++;
	rx->interrupts++;
	mod_delayed_work(system_wq, &rx->work, 0);
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	return IRQ_HANDLED;
}

void rtl_ax_rx_open(struct rtl_ax_rx *rx)
{
	const struct rtl_ax_io *io = rx->io;
	unsigned long flags;

	spin_lock_irqsave(&rx->irq_lock, flags);
	io->write32(io->ctx, 0x10b4, RTL_AX_RX_IRQS);
	io->write32(io->ctx, 0x01a4, BIT(21));
	io->write32(io->ctx, 0x10b0, rx->irq_mask);
	io->write32(io->ctx, 0x01a0, BIT(21));
	rtl_ax_update32(io, RTL_AX_HAXI_CONFIG, RTL_AX_BIT(17), RTL_AX_BIT(15));
	rx->running = true;
	io->write32(io->ctx, 0x30b0, RTL_AX_IRQ_INDICATION);
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	/* Drain C2H already queued during WCPU/MAC startup as well. */
	mod_delayed_work(system_wq, &rx->work, 0);
}

void rtl_ax_rx_close(struct rtl_ax_rx *rx)
{
	unsigned long flags;

	spin_lock_irqsave(&rx->irq_lock, flags);
	rx->running = false;
	rx->io->write32(rx->io->ctx, 0x30b0, 0);
	rx->io->write32(rx->io->ctx, 0x10b0, 0);
	rx->io->write32(rx->io->ctx, 0x01a0, 0);
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	if (rx->irq_requested)
		synchronize_irq(rx->irq);
	cancel_delayed_work_sync(&rx->work);
	rtl_ax_update32(rx->io, RTL_AX_HAXI_CONFIG, RTL_AX_BIT(15), 0);
}

void rtl_ax_rx_release_interrupts(struct rtl_ax_rx *rx)
{
	if (rx->irq_requested) {
		free_irq(rx->irq, rx);
		rx->irq_requested = false;
	}
	if (rx->vectors) {
		pci_free_irq_vectors(rx->pdev);
		rx->vectors = false;
	}
}

void rtl_ax_rx_free(struct rtl_ax_rx *rx)
{
	unsigned int number;

	rtl_ax_rx_release_interrupts(rx);
	for (number = 0; number < 2; number++) {
		struct rtl_ax_rx_queue *queue = &rx->queues[number];
		struct device *device = rx->pe_device;

		if (queue->buffers)
			dma_free_coherent(device, (RTL_AX_RX_ENTRIES + (number ? 0 : RTL_AX_RX_SPARES)) * queue->buffer_bytes,
					  queue->buffers, queue->buffer_dma);
		if (queue->descriptors)
			dma_free_coherent(number ? device : rx->pe_device, RTL_AX_RX_ENTRIES * 8,
					  queue->descriptors, queue->descriptor_dma);
	}
	if (rx->uplink_states) dma_free_coherent(rx->pe_device, SZ_128K, rx->uplink_states, rx->uplink_states_dma);
	if (rx->pe_forward) dma_free_coherent(rx->pe_device, SZ_128K, rx->pe_forward, rx->pe_forward_dma);
	put_device(rx->pe_device);
	kfree(rx);
}

struct rtl_ax_rx *rtl_ax_rx_prepare(struct pci_dev *pdev, const struct rtl_ax_io *io,
				  rtl_ax_rx_handler handler, void *ctx, struct device *rpq_device)
{
	struct rtl_ax_rx *rx;
	unsigned int number, entry;
	int ret;

	rx = kzalloc(sizeof(*rx), GFP_KERNEL);
	if (!rx)
		return ERR_PTR(-ENOMEM);
	rx->pdev = pdev;
	rx->rpq_device = rpq_device;
	rx->pe_device = get_device(rpq_device);
	rx->irq_mask = RTL_AX_RX_IRQS;
	rx->io = io;
	rx->handler = handler;
	rx->handler_ctx = ctx;
	spin_lock_init(&rx->irq_lock);
	mutex_init(&rx->uplink_lock);
	INIT_DELAYED_WORK(&rx->work, rtl_ax_rx_work);
	io->write32(io->ctx, 0x30b0, 0);
	for (number = 0; number < 2; number++) {
		struct rtl_ax_rx_queue *queue = &rx->queues[number];
		/* ARM, PCIe and PE share RX payloads without cached ARM aliases. */
		struct device *device = rx->pe_device;

		queue->buffer_bytes = number ? RTL_AX_RPQ_BUFFER_BYTES : RTL_AX_RXQ_BUFFER_BYTES;
		/* PE refills RXBD addresses; use its coherent descriptor allocation. */
		queue->descriptors = dma_alloc_coherent(number ? device : rx->pe_device, RTL_AX_RX_ENTRIES * 8,
						      &queue->descriptor_dma, GFP_KERNEL);
		queue->buffers = dma_alloc_coherent(device, (RTL_AX_RX_ENTRIES + (number ? 0 : RTL_AX_RX_SPARES)) * queue->buffer_bytes,
						  &queue->buffer_dma, GFP_KERNEL);
		if (!queue->descriptors || !queue->buffers) {
			ret = -ENOMEM;
			goto free;
		}
		memset(queue->buffers, 0, (RTL_AX_RX_ENTRIES + (number ? 0 : RTL_AX_RX_SPARES)) * queue->buffer_bytes);
		for (entry = 0; entry < RTL_AX_RX_ENTRIES; entry++)
			rtl_ax_rxbd(queue->descriptors + entry * 8,
				    lower_32_bits(queue->buffer_dma + entry * queue->buffer_bytes),
				    queue->buffer_bytes);
	}
	rx->uplink_states = dma_alloc_coherent(rpq_device, SZ_128K, &rx->uplink_states_dma, GFP_KERNEL);
	if (!rx->uplink_states) { ret = -ENOMEM; goto free; }
	memset(rx->uplink_states, 0, SZ_128K);
	rx->pe_forward = dma_alloc_coherent(rpq_device, SZ_128K, &rx->pe_forward_dma, GFP_KERNEL);
	if (!rx->pe_forward) { ret = -ENOMEM; goto free; }
	dma_wmb();
	ret = rtl_ax_rx_configure(io, lower_32_bits(rx->queues[0].descriptor_dma),
				lower_32_bits(rx->queues[1].descriptor_dma));
	if (ret)
		goto free;
	ret = pci_alloc_irq_vectors(pdev, 1, 1, PCI_IRQ_MSI | PCI_IRQ_INTX);
	if (ret < 0)
		goto free;
	rx->vectors = true;
	rx->irq = pci_irq_vector(pdev, 0);
	ret = request_irq(rx->irq, rtl_ax_rx_interrupt, pdev->msi_enabled ? 0 : IRQF_SHARED,
			  "rtl_ax_ap", rx);
	if (ret)
		goto free;
	rx->irq_requested = true;
	return rx;
free:
	rtl_ax_rx_free(rx);
	return ERR_PTR(ret);
}

int rtl_ax_rx_status(struct rtl_ax_rx *rx, char *out, size_t capacity)
{
	static const char * const encodings[] = { "legacy", "HT", "VHT", "HE" };
	unsigned int type, subtype;
	int length = scnprintf(out, capacity,
		"rxq=%llu rpq=%llu rx_malformed=%llu rx_tag_pending=%llu rx_unhandled=%llu "
		"irq=%llu rdu=%llu halt=0x%08x\n",
		rx->queues[0].packets, rx->queues[1].packets,
		rx->queues[0].malformed + rx->queues[1].malformed,
		rx->queues[0].tag_pending + rx->queues[1].tag_pending,
		rx->queues[0].unhandled + rx->queues[1].unhandled,
		rx->interrupts, rx->unavailable, rx->halt_error);
	length += scnprintf(out + length, capacity - length,
		"PE_uplink_frames=%u bytes=%u VP_producer=%u completed=%u slow=%u/%u AMSDU=%u\n",
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2180) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2184) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2168) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x216c) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2178) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x217c) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x21c4) : 0);
	if (rx->rpq_memory && get_unaligned_le32(rx->rpq_memory + 0x2160)) {
		u32 start = get_unaligned_le32(rx->rpq_memory + 0x21d8);
		length += scnprintf(out + length, capacity - length,
			"VP%u_submitted=%u completed=%u start=%u\n",
			(get_unaligned_le32(rx->rpq_memory + 0x2160) - 0xb3001100) / 0xa0,
			get_unaligned_le32(rx->rpq_memory + 0x2168) - start,
			get_unaligned_le32(rx->rpq_memory + 0x216c) - start, start);
	}
	if (rx->rpq_memory) {
		unsigned int macid, tid;
		length += scnprintf(out + length, capacity - length,
			"PE_uplink_slow_pending=%u slow_sleep=%u ARM_data=%llu ARM_data_bytes=%llu ARM_PM_data=%llu\n",
			get_unaligned_le32(rx->rpq_memory + 0x21e0),
			get_unaligned_le32(rx->rpq_memory + 0x21e4),
			rx->pe_rx_data, rx->pe_rx_data_bytes, rx->pe_rx_pm_data);
		length += scnprintf(out + length, capacity - length,
			"PE_uplink_slow_format=%u unseeded=%u sequence_gap=%u PN_old=%u NI_full=%u\n",
			get_unaligned_le32(rx->rpq_memory + 0x21ac),
			get_unaligned_le32(rx->rpq_memory + 0x21b0),
			get_unaligned_le32(rx->rpq_memory + 0x21b4),
			get_unaligned_le32(rx->rpq_memory + 0x21b8),
			get_unaligned_le32(rx->rpq_memory + 0x21bc));
		length += scnprintf(out + length, capacity - length,
			"PE_RXQ_buffer_pool=%u prefetch_held=%u free_replacements=%u NI_retained_descriptors=2 completion_basis=read_pointer\n",
			RTL_AX_RX_ENTRIES + RTL_AX_RX_SPARES,
			!!get_unaligned_le32(rx->rpq_memory + 0x17810) +
			!!get_unaligned_le32(rx->rpq_memory + 0x17818),
			!!get_unaligned_le32(rx->rpq_memory + 0x17800) +
			!!get_unaligned_le32(rx->rpq_memory + 0x17804));
		for (macid = 1; macid < 128; macid++) {
			u32 frames = 0, active_tids = 0;
			if (!rx->keys[macid]) continue;
			for (tid = 0; tid < 16; tid++) {
				u32 count = get_unaligned_le32(rx->uplink_states + (macid * 16 + tid) * 32 + 12);
				frames += count;
				if (count) active_tids |= BIT(tid);
			}
			length += scnprintf(out + length, capacity - length,
				"PE_uplink_peer=%u frames=%u TIDs=%04x\n", macid, frames, active_tids);
		}
	}
	length += scnprintf(out + length, capacity - length,
		"PE_RXQ_scanned=%u ARM_delivered=%llu malformed=%llu released=%u/%u\n",
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2158) : 0,
		rx->pe_rx_delivered, rx->pe_rx_malformed,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x214c) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2150) : 0);
	length += scnprintf(out + length, capacity - length,
		"PE_RPQ_owned=%u forwarded=%u/%u consumer=%u tag=%u malformed=%u\n",
		rx->rpq_owned,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2124) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2128) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2118) : rx->queues[1].consumer,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x211c) : rx->queues[1].tag,
		rx->rpq_memory ? get_unaligned_le32(rx->rpq_memory + 0x2134) : 0);
	length += scnprintf(out + length, capacity - length,
		"RPQ_hw=%08x desc_dma=%08x buffer_dma=%08x\n",
		rx->io->read32(rx->io->ctx, 0x121c), (u32)rx->queues[1].descriptor_dma, (u32)rx->queues[1].buffer_dma);
	length += scnprintf(out + length, capacity - length,
		"running=%u RXQ_consumer=%u RPQ_consumer=%u 80211_beacons=%llu "
		"80211_CRC_OK=%llu 80211_CRC_error=%llu\n",
		rx->running, rx->queues[0].consumer, rx->queues[1].consumer,
		rx->queues[0].beacons + rx->queues[1].beacons,
		rx->queues[0].wifi_crc_ok + rx->queues[1].wifi_crc_ok,
		rx->queues[0].wifi_crc_error + rx->queues[1].wifi_crc_error);
	length += scnprintf(out + length, capacity - length,
		"80211_ICV_error=%llu data_FCS_OK_ICV_error=%llu\n",
		rx->queues[0].wifi_icv_error + rx->queues[1].wifi_icv_error,
		rx->queues[0].wifi_data_icv_crc_ok + rx->queues[1].wifi_data_icv_crc_ok);
	for (type = 0; type < 16; type++)
		length += scnprintf(out + length, capacity - length, "descriptor_type=%u packets=%llu\n",
			type, rx->queues[0].types[type] + rx->queues[1].types[type]);
	for (type = 0; type < 4; type++) {
		length += scnprintf(out + length, capacity - length,
			"80211_encoding=%s CRC_OK=%llu data_not_null=%llu data_software_FCS_OK=%llu data_software_FCS_error=%llu\n", encodings[type],
			rx->queues[0].wifi_encoding[type] + rx->queues[1].wifi_encoding[type],
			rx->queues[0].wifi_data_encoding[type] + rx->queues[1].wifi_data_encoding[type],
			rx->queues[0].data_fcs_ok[type] + rx->queues[1].data_fcs_ok[type],
			rx->queues[0].data_fcs_error[type] + rx->queues[1].data_fcs_error[type]);
		for (subtype = 0; subtype < 16; subtype++) {
			u64 packets = rx->queues[0].wifi_frames[type][subtype] +
				rx->queues[1].wifi_frames[type][subtype];

			if (packets)
				length += scnprintf(out + length, capacity - length,
					"80211_type=%u subtype=%u CRC_OK=%llu\n", type, subtype, packets);
		}
	}
	return length;
}

int rtl_ax_rx_data_observation(struct rtl_ax_rx *rx, char *out, size_t capacity)
{
	struct rtl_ax_data_observation observations[4];
	unsigned long flags;
	unsigned int encoding;
	int length = 0;

	spin_lock_irqsave(&rx->irq_lock, flags);
	memcpy(observations, rx->queues[0].last_data, sizeof(observations));
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	for (encoding = 0; encoding < 4; encoding++) {
		struct rtl_ax_data_observation *last = &observations[encoding];

		length += scnprintf(out + length, capacity - length,
			"encoding=%u sequence=%llu bytes=%u driver_info=%u payload_offset=%u computed_FCS=%08x frame_FCS=%08x\n",
			encoding, last->sequence, last->bytes, last->info_bytes, last->payload_offset,
			last->computed_fcs, last->frame_fcs);
		length += scnprintf(out + length, capacity - length,
			"WD=%08x,%08x,%08x,%08x,%08x,%08x,%08x,%08x prefix=%*phN\n",
			last->words[0], last->words[1], last->words[2], last->words[3],
			last->words[4], last->words[5], last->words[6], last->words[7],
			last->prefix_bytes, last->prefix);
	}
	return length;
}
