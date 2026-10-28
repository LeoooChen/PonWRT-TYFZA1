// SPDX-License-Identifier: GPL-2.0-only
/* Native RXQ/RPQ ownership and packet dispatch,following the board's
 * hal_check_rxrdy/hal_handle_rxbd_info/update_rxbd/notify_rxdone path. */
#include <linux/dma-mapping.h>
#include <linux/crc32.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pci.h>
#include <linux/slab.h>
#include <linux/sizes.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
#include <linux/unaligned.h>
#include <net/mac80211.h>
#include "receive.h"
#include "rx.h"
#include "pe_probe.h"

extern bool rtl_ax_datapath_profile;

#define RTL_AX_RX_IRQS (BIT(20) | BIT(19) | BIT(2) | BIT(0))
#define RTL_AX_IRQ_INDICATION (BIT(29) | BIT(24))

static bool rx_packet_trace;
module_param(rx_packet_trace, bool, 0644);
MODULE_PARM_DESC(rx_packet_trace, "Capture RX packet prefixes and software FCS for diagnosis");

struct rtl_ax_data_observation {
	u64 sequence;
	u32 words[8], bytes, info_bytes, payload_offset;
	u32 computed_fcs, frame_fcs;
	u8 prefix[64], prefix_bytes;
};

struct rtl_ax_rx_buffer {
	u8 *data;
	dma_addr_t dma;
	struct sk_buff *skb;
};

struct rtl_ax_rx_queue {
	u8 *descriptors, *buffers;
	dma_addr_t descriptor_dma, buffer_dma;
	struct rtl_ax_rx_buffer *payloads;
	u32 *addresses;
	dma_addr_t addresses_dma;
	u16 slot_ids[RTL_AX_RX_ENTRIES];
	unsigned int allocated_payloads;
	unsigned int consumer, tag, buffer_bytes;
	u64 packets, malformed, tag_pending, unhandled;
	u64 types[16], beacons, wifi_crc_ok, wifi_crc_error;
	u64 unavailable, data_bytes, skb_transfers, alloc_failed, skb_prepare_ns;
	u64 wifi_icv_error, wifi_data_icv_crc_ok;
	u64 wifi_frames[4][16], wifi_encoding[4], wifi_data_encoding[4];
	u64 data_fcs_ok[4], data_fcs_error[4];
	struct rtl_ax_data_observation last_data[4];
};

struct rtl_ax_shortcut_seen { u32 frames, bytes, msdus; };

struct rtl_ax_rx {
	struct pci_dev *pdev;
	struct pe_probe pe;
	u32 pe_command;
	struct device *rpq_device;
	struct device *pe_device;
	void *rpq_memory, *pe_forward;
	dma_addr_t pe_forward_dma;
	bool rpq_owned, rpq_transfer;
	u32 irq_mask;
	const struct rtl_ax_io *io;
	struct rtl_ax_rx_queue queues[2];
	u32 dfs_epoch[4];
	rtl_ax_rx_handler handler;
	void *handler_ctx;
	struct delayed_work work;
	spinlock_t irq_lock;
	bool running, irq_requested, vectors;
	int irq;
	u64 interrupts;
	u32 halt_error;
	void *uplink_states;
	dma_addr_t uplink_states_dma;
	struct ieee80211_hw *hw;
	struct ieee80211_key_conf *keys[128];
	struct mutex uplink_lock;
	unsigned int uplink_wifi_id;
	u8 uplink_trigger_tids[128];
	u16 uplink_pending_tids[128];
	struct rtl_ax_shortcut_seen uplink_seen[128][16];
	u32 uplink_forwarded_seen, pe_rdu_seen[2], pe_capture_seen;
	u64 pe_rdu[2], pe_rx_delivered, pe_rx_malformed, pe_captured;
	bool pool_initialized;
};

static struct rtl_ax_rx_buffer rtl_ax_rx_slot_buffer(struct rtl_ax_rx *rx,
	unsigned int number, unsigned int slot)
{
	struct rtl_ax_rx_queue *queue = &rx->queues[number];

	if (number)
		return (struct rtl_ax_rx_buffer){queue->buffers + slot * queue->buffer_bytes,
			queue->buffer_dma + slot * queue->buffer_bytes};
	return queue->payloads[queue->slot_ids[slot]];
}

/* RX work, PE and PCI DMA are paused. Mark each ring's old prefix, using
 * the PE scan cursor rather than the RXBD return cursor for owned queues. */
void rtl_ax_rx_dfs_epoch(struct rtl_ax_rx *rx)
{
	for (unsigned int number = 0; number < 2; number++) {
		unsigned int consumer = rx->queues[number].consumer;
		unsigned int producer = (rx->io->read32(rx->io->ctx, 0x1218 + number * 4) >> 16) & 0x3ff;

		if (rx->rpq_owned)
			consumer = get_unaligned_le32((number ? rx->rpq_memory : rx->pe.memory) + (number ? 0x2118 : 0x2144));
		rx->dfs_epoch[number] = producer | (consumer != producer ? 256 : 0);
		if (number && rx->rpq_owned) {
			u8 *mailbox = rx->rpq_memory + 0x2000;
			u32 read = get_unaligned_le32(mailbox + 296);
			u32 write = get_unaligned_le32(mailbox + 292);
			put_unaligned_le32(rx->dfs_epoch[0], rx->pe.memory + 0x2000 + RTL_AX_PE_DFS_EPOCH * 4);
			put_unaligned_le32(rx->dfs_epoch[1], mailbox + (RTL_AX_PE_DFS_EPOCH + 1) * 4);
			rx->dfs_epoch[3] = write | (read != write ? 256 : 0);
		}
	}
	dma_wmb();
}

#include "rx-offload-state.h"

static int rtl_ax_rx_dispatch(struct rtl_ax_rx *rx, bool old, const struct rtl_ax_rx_packet *packet)
{
	if (packet->type == 8 && old) return 0;
	return rx->handler(rx->handler_ctx, packet);
}

static int rtl_ax_rx_buffer_alloc(struct rtl_ax_rx *rx, unsigned int bytes,
	gfp_t flags, struct rtl_ax_rx_buffer *buffer)
{
	struct sk_buff *skb = alloc_skb(bytes + NET_SKB_PAD, flags);
	dma_addr_t dma;

	if (!skb) return -ENOMEM;
	skb_reserve(skb, NET_SKB_PAD);
	put_unaligned_le32(0, skb->data);
	dma = dma_map_single(rx->pe_device, skb->data, bytes, DMA_BIDIRECTIONAL);
	if (dma_mapping_error(rx->pe_device, dma)) {
		kfree_skb(skb);
		return -EIO;
	}
	*buffer = (struct rtl_ax_rx_buffer){ .data = skb->data, .dma = dma, .skb = skb };
	return 0;
}

/* RXQ transfers the DMA-backed linear skb, as in the factory ARM path. */
static struct sk_buff *rtl_ax_rx_take_skb(struct rtl_ax_rx *rx,
	const struct rtl_ax_rx_packet *packet)
{
	struct rtl_ax_rx_queue *q = &rx->queues[0];
	unsigned int id = q->slot_ids[q->consumer];
	struct rtl_ax_rx_buffer old = q->payloads[id], replacement;
	bool profile = READ_ONCE(rtl_ax_datapath_profile);
	u64 begin = profile ? ktime_get_ns() : 0;
	unsigned long flags;

	if (rtl_ax_rx_buffer_alloc(rx, q->buffer_bytes, GFP_ATOMIC, &replacement))
		return NULL;
	/* The completed RXBD was already synced for CPU before WD parsing.
	 * Its slot is not returned to hardware until replacement is published. */
	dma_unmap_single_attrs(rx->pe_device, old.dma, q->buffer_bytes,
		DMA_BIDIRECTIONAL, DMA_ATTR_SKIP_CPU_SYNC);
	skb_reserve(old.skb, packet->payload - old.data);
	skb_put(old.skb, packet->bytes);
	spin_lock_irqsave(&rx->irq_lock, flags);
	q->payloads[id] = replacement;
	WRITE_ONCE(q->addresses[id], cpu_to_le32(replacement.dma));
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	q->skb_transfers++;
	if (profile) q->skb_prepare_ns += ktime_get_ns() - begin;
	return old.skb;
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
		bool old, replaced = false;
		struct rtl_ax_rx_buffer payload;
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
		payload = rtl_ax_rx_slot_buffer(rx, number, queue->consumer);
		buffer = payload.data;
		/* The actual board function polls10000 coherent tag observations,
		 * wraps8191→1 and changes its saved tag only after a match. */
		for (attempt = 0; attempt < 10000; attempt++) {
			if (!number) dma_sync_single_for_cpu(rx->pe_device, payload.dma,
				queue->buffer_bytes, DMA_BIDIRECTIONAL);
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
		old = rtl_ax_rx_epoch_old(&rx->dfs_epoch[number], queue->consumer);
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
					queue->data_bytes += packet.bytes;
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
			if (!number && !packet.type) {
				packet.skb = rtl_ax_rx_take_skb(rx, &packet);
				if (!packet.skb) { queue->alloc_failed++; goto refill; }
				replaced = true;
			}
			ret = rtl_ax_rx_dispatch(rx, old, &packet);
			if (ret && packet.skb) dev_kfree_skb_any(packet.skb);
			if (ret == -EOPNOTSUPP)
				queue->unhandled++;
			else if (ret)
				queue->malformed++;
		}
	refill:
		payload = rtl_ax_rx_slot_buffer(rx, number, queue->consumer);
		if (!replaced) {
			WRITE_ONCE(*(__le32 *)payload.data, 0);
			if (!number) dma_sync_single_for_device(rx->pe_device, payload.dma,
				queue->buffer_bytes, DMA_BIDIRECTIONAL);
		}
		rtl_ax_rxbd(queue->descriptors + queue->consumer * 8,
			    lower_32_bits(payload.dma),
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
		bool old = rtl_ax_rx_epoch_old(&rx->dfs_epoch[3], consumer);
		dma_rmb();
		if (!rtl_ax_receive_packet(rx->pe_forward + consumer * RTL_AX_RPQ_BUFFER_BYTES, RTL_AX_RPQ_BUFFER_BYTES, &packet))
			rtl_ax_rx_dispatch(rx, old, &packet);
		consumer = (consumer + 1) & 255;
		dma_wmb();
		put_unaligned_le32(consumer, mailbox + 296);
	}
}

static void rtl_ax_rx_pe_deliver(struct rtl_ax_rx *rx, unsigned int work)
{
	struct rtl_ax_rx_packet packet = {};
	struct rtl_ax_rx_queue *queue = &rx->queues[0];
	u32 item = get_unaligned_le32(rx->pe.memory + RTL_AX_RX_WORK_IDS + work * 4);
	struct rtl_ax_rx_buffer payload = queue->payloads[item & ~RTL_AX_RX_WORK_OLD];
	dma_addr_t dma = payload.dma;
	u8 *buffer = payload.data;
	int ret;

	rtl_ax_rx_uplink_sync(rx);
	dma_sync_single_for_cpu(rx->pe_device, dma, queue->buffer_bytes, DMA_BIDIRECTIONAL);
	ret = rtl_ax_receive_packet(buffer, queue->buffer_bytes, &packet);
	if (!ret) {

		rtl_ax_rx_dispatch(rx, !!(item & RTL_AX_RX_WORK_OLD), &packet);
		rtl_ax_rx_uplink_seed(rx, &packet);
		rx->pe_rx_delivered++;
	} else rx->pe_rx_malformed++;
	/* Publish PN/SN and discard ARM cache aliases before PE recycles payload. */
	dma_sync_single_for_device(rx->pe_device, dma, queue->buffer_bytes, DMA_BIDIRECTIONAL);
	dma_wmb();
	put_unaligned_le32(BIT(30), rx->pe.memory + RTL_AX_RX_WORK_STATUS + work * 4);
}

static void rtl_ax_rx_pe_rxq_drain(struct rtl_ax_rx *rx)
{
	u8 *mailbox = rx->pe.memory + 0x2000;
	u32 consumer = get_unaligned_le32(mailbox + 380);
	u32 budget = 64;

	while (budget-- && consumer != get_unaligned_le32(mailbox + 376)) {
		dma_rmb();
		u32 work = get_unaligned_le32(rx->pe.memory + RTL_AX_RX_ARM_RING + consumer * 4);
		rtl_ax_rx_pe_deliver(rx, work);
		consumer = (consumer + 1) & 255;
		dma_wmb();
		put_unaligned_le32(consumer, mailbox + 380);
	}
}

static void rtl_ax_rx_rdu_sync(struct rtl_ax_rx *rx)
{
	unsigned long flags;

	spin_lock_irqsave(&rx->irq_lock, flags);
	if (rx->pe.memory)
		for (unsigned int i = 0; i < 2; i++) {
			u32 count = get_unaligned_le32(rx->pe.memory + 0x2000 + (RTL_AX_RX_RDU + i) * 4);
			rx->pe_rdu[i] += count - rx->pe_rdu_seen[i];
			rx->pe_rdu_seen[i] = count;
		}
	if (rx->pe.memory) {
		u32 captured = get_unaligned_le32(rx->pe.memory + 0x2158);
		rx->pe_captured += captured - rx->pe_capture_seen;
		rx->pe_capture_seen = captured;
	}
	spin_unlock_irqrestore(&rx->irq_lock, flags);
}

void *rtl_ax_rx_pe_memory(struct rtl_ax_rx *rx) { return rx->pe.memory; }

int rtl_ax_rx_pe_pause(struct rtl_ax_rx *rx)
{
	u32 acknowledged, command;

	if (!rx || !rx->pe.attached) return 0;
	command = rx->pe_command += 2;
	put_unaligned_le32(command, rx->pe.memory + 0x200c);
	dma_wmb();
	return read_poll_timeout(get_unaligned_le32, acknowledged, acknowledged == command,
		10, 100000, false, rx->pe.memory + 0x2010);
}

void rtl_ax_rx_pe_open(struct rtl_ax_rx *rx)
{
	if (!rx || !rx->pe.attached) return;
	rx->pe_command += 2;
	put_unaligned_le32(rx->pe_command | 1, rx->pe.memory + 0x200c);
	dma_wmb();
}

void rtl_ax_rx_pe_event_ack(struct rtl_ax_rx *rx)
{
	if (rx && READ_ONCE(rx->pe.attached))
		put_unaligned_le32(get_unaligned_le32(rx->pe.memory + 0x2114), rx->pe.memory + 0x2108);
}

void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx)
{
	if (READ_ONCE(rx->running)) mod_delayed_work(system_wq, &rx->work, 0);
}

int rtl_ax_rx_pe_rpq_start(struct rtl_ax_rx *rx, void *tx_memory, u32 tx_dma,
	const u32 *program, unsigned int bytes)
{
	void *memory = rx->pe.memory;
	u8 *mailbox = memory + 0x2000, *tx_mailbox = tx_memory + 0x2000;
	int ret;
	struct rtl_ax_rx_queue *q = &rx->queues[0];
	unsigned long flags;
	bool mapped[RTL_AX_RX_BUFFERS] = {};
	unsigned int i, idle = 0;

	spin_lock_irqsave(&rx->irq_lock, flags);
	rx->rpq_transfer = true;
	rx->irq_mask = 0;
	rx->io->write32(rx->io->ctx, 0x10b0, 0);
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	synchronize_irq(rx->irq);
	cancel_delayed_work_sync(&rx->work);
	rx->rpq_memory = tx_memory;
	/* DMA-mapped skbs remain device-owned while PE reads their uncached alias.
	 * Only ARM exceptions acquire a CPU mapping, and release it before ACK. */
	for (i = 0; i < RTL_AX_RX_BUFFERS; i++)
		dma_sync_single_for_device(rx->pe_device, q->payloads[i].dma,
			q->buffer_bytes, DMA_BIDIRECTIONAL);
	dma_sync_single_for_device(rx->pe_device, rx->queues[1].buffer_dma,
		RTL_AX_RX_ENTRIES * RTL_AX_RPQ_BUFFER_BYTES, DMA_BIDIRECTIONAL);
	for (i = 0; i < RTL_AX_RX_ENTRIES; i++) {
		put_unaligned_le32(q->slot_ids[i], memory + RTL_AX_RX_SLOT_MAP + i * 4);
		mapped[q->slot_ids[i]] = true;
	}
	/* Preserve NI's two retained payloads across a stopped/restarted VP. */
	if (!rx->pool_initialized) {
		memset(memory + RTL_AX_RX_NI_TAIL, 0, 16);
		for (i = 0; i < RTL_AX_RX_BUFFERS; i++)
			if (!mapped[i]) put_unaligned_le32(i, memory + RTL_AX_RX_IDLE_IDS + idle++ * 4);
		put_unaligned_le32(idle, mailbox + RTL_AX_RX_IDLE_COUNT * 4);
		rx->pool_initialized = true;
	}
	memset(memory + RTL_AX_RX_WORK_STATUS, 0, 0x800);
	memset(memory + RTL_AX_RX_REORDER_SEARCH, 0, 12);
	for (i = RTL_AX_RX_WORK_WRITE; i <= RTL_AX_RX_WORK_READ; i++)
		put_unaligned_le32(0, mailbox + i * 4);
	for (i = 0; i < 2; i++) {
		put_unaligned_le32(rx->dfs_epoch[i], mailbox + (RTL_AX_PE_DFS_EPOCH + i) * 4);
		put_unaligned_le32(0, mailbox + (RTL_AX_RX_RDU + i) * 4);
		rx->pe_rdu_seen[i] = 0;
		rx->dfs_epoch[2 + i] = 0;
	}
	put_unaligned_le32(0, mailbox + RTL_AX_RX_ARM_BARRIER * 4);
	put_unaligned_le32(q->descriptor_dma, mailbox + 134 * 4);
	put_unaligned_le32(q->consumer, mailbox + 324);
	put_unaligned_le32(q->tag, mailbox + 328);
	put_unaligned_le32(q->consumer, mailbox + 332);
	put_unaligned_le32(q->consumer, mailbox + 336);
	put_unaligned_le32(q->consumer, mailbox + RTL_AX_RX_HW_PRODUCER * 4);
	put_unaligned_le32(0, mailbox + 376);
	put_unaligned_le32(0, mailbox + 380);
	put_unaligned_le32(rx->queues[1].consumer, tx_mailbox + 280);
	put_unaligned_le32(rx->queues[1].tag, tx_mailbox + 284);
	put_unaligned_le32(rx->pe_forward_dma, tx_mailbox + 288);
	put_unaligned_le32(0, tx_mailbox + 292);
	put_unaligned_le32(0, tx_mailbox + 296);
	put_unaligned_le32(get_unaligned_le32(tx_mailbox + 8), mailbox + 8);
	put_unaligned_le32(tx_dma, mailbox + 187 * 4);
	put_unaligned_le32(rx->pe_command & ~1U, mailbox + 12);
	put_unaligned_le32(get_unaligned_le32(tx_mailbox + 256), mailbox + 256);
	put_unaligned_le32(get_unaligned_le32(mailbox + 268), mailbox + 264);
	put_unaligned_le32(get_unaligned_le32(mailbox + 268), mailbox + 276);
	put_unaligned_le32(rx->dfs_epoch[1], tx_mailbox + (RTL_AX_PE_DFS_EPOCH + 1) * 4);
	rx->rpq_owned = true;
	dma_wmb();
	put_unaligned_le32(rx->queues[1].buffer_dma, tx_mailbox + 272);
	put_unaligned_le32(0, tx_mailbox + 320);
	put_unaligned_le32(q->addresses_dma, mailbox + 320);
	rx->irq_mask = 0;
	smp_store_release(&rx->rpq_transfer, false);
	rx->io->write32(rx->io->ctx, 0x30b0, RTL_AX_IRQ_INDICATION);
	ret = pe_probe_start(&rx->pe, program, bytes);
	if (!ret) {
		put_unaligned_le32(rx->pe.dma + PE_PROBE_MAILBOX,
			tx_mailbox + RTL_AX_RX_EVENT_CONTEXT * 4);
		dma_wmb();
	}
	rtl_ax_rx_pe_wake(rx);
	return ret;
}

static void rtl_ax_rx_idle_put(struct rtl_ax_rx *rx, u32 id)
{
	u8 *memory = rx->pe.memory;
	struct rtl_ax_rx_buffer payload = rx->queues[0].payloads[id];
	u32 count = get_unaligned_le32(memory + 0x2000 + RTL_AX_RX_IDLE_COUNT * 4);

	WRITE_ONCE(*(__le32 *)payload.data, 0);
	dma_sync_single_for_device(rx->pe_device, payload.dma,
		RTL_AX_RXQ_BUFFER_BYTES, DMA_BIDIRECTIONAL);
	put_unaligned_le32(id, memory + RTL_AX_RX_IDLE_IDS + count * 4);
	put_unaligned_le32(count + 1, memory + 0x2000 + RTL_AX_RX_IDLE_COUNT * 4);
}

static void rtl_ax_rx_tail_recycle(struct rtl_ax_rx *rx)
{
	u8 *memory = rx->pe.memory;
	u32 read = get_unaligned_le32(memory + 0x216c);
	for (unsigned int i = 0; i < 2; i++) {
		u8 *held = memory + RTL_AX_RX_NI_TAIL + i * 8;
		u32 id = get_unaligned_le32(held);
		if (!id || (s32)((read - get_unaligned_le32(held + 4) - 2) << 1) < 0) continue;
		rtl_ax_rx_idle_put(rx, id - 1);
		put_unaligned_le32(0, held);
	}
}

static void rtl_ax_rx_work_recycle(struct rtl_ax_rx *rx)
{
	u8 *memory = rx->pe.memory, *mailbox = memory + 0x2000;
	u32 read = get_unaligned_le32(mailbox + RTL_AX_RX_WORK_READ * 4);
	u32 process = get_unaligned_le32(mailbox + RTL_AX_RX_WORK_PROCESS * 4);

	rtl_ax_rx_tail_recycle(rx);
	while (read != process) {
		u32 owner = get_unaligned_le32(memory + RTL_AX_RX_WORK_STATUS + read * 4);
		u32 id = get_unaligned_le32(memory + RTL_AX_RX_WORK_IDS + read * 4) & ~RTL_AX_RX_WORK_OLD;
		if ((owner & BIT(31)) &&
		    (s32)((get_unaligned_le32(mailbox + 364) - (owner & 0x7fffffff) - 2) << 1) < 0) {
			unsigned int i = get_unaligned_le32(memory + RTL_AX_RX_NI_TAIL) ? 1 : 0;
			put_unaligned_le32(id + 1, memory + RTL_AX_RX_NI_TAIL + i * 8);
			put_unaligned_le32(owner & 0x7fffffff, memory + RTL_AX_RX_NI_TAIL + i * 8 + 4);
		} else rtl_ax_rx_idle_put(rx, id);
		put_unaligned_le32(0, memory + RTL_AX_RX_WORK_STATUS + read * 4);
		read = (read + 1) & 255;
	}
	put_unaligned_le32(read, mailbox + RTL_AX_RX_WORK_READ * 4);
}

int rtl_ax_rx_pe_rpq_stop(struct rtl_ax_rx *rx)
{
	u8 *mailbox, *tx_mailbox;
	int ret;
	unsigned long flags;

	if (!rx->rpq_memory) return 0;
	/* Caller has paused PE, so consumer/tag can transfer back to the host. */
	WRITE_ONCE(rx->rpq_transfer, true);
	cancel_delayed_work_sync(&rx->work);
	mailbox = rx->pe.memory + 0x2000;
	tx_mailbox = rx->rpq_memory + 0x2000;
	rtl_ax_rx_rdu_sync(rx);
	/* PE is paused; these producers cannot advance during the handoff. */
	do { rtl_ax_rx_pe_drain(rx); }
	while (get_unaligned_le32(tx_mailbox + 296) != get_unaligned_le32(tx_mailbox + 292));
	do { rtl_ax_rx_pe_rxq_drain(rx); }
	while (get_unaligned_le32(mailbox + 380) != get_unaligned_le32(mailbox + 376));
	rtl_ax_rx_uplink_sync(rx);
	/* Sorted NI submissions can be beyond the contiguous process cursor. */
	{
		u32 read = get_unaligned_le32(mailbox + RTL_AX_RX_WORK_READ * 4);
		u32 write = get_unaligned_le32(mailbox + RTL_AX_RX_WORK_WRITE * 4);
		for (; read != write; read = (read + 1) & 255) {
			u32 owner = get_unaligned_le32(rx->pe.memory + RTL_AX_RX_WORK_STATUS + read * 4);
			if ((owner & BIT(31)) &&
			    (s32)((get_unaligned_le32(mailbox + 364) - (owner & 0x7fffffff)) << 1) < 0)
				return -EBUSY;
		}
	}
	/* PE captured these buffers before pausing; dispatch them in capture order. */
	{
		u32 process = get_unaligned_le32(mailbox + RTL_AX_RX_WORK_PROCESS * 4);
		while (process != get_unaligned_le32(mailbox + RTL_AX_RX_WORK_WRITE * 4)) {
			if (!(get_unaligned_le32(rx->pe.memory + RTL_AX_RX_WORK_STATUS + process * 4) & BIT(31)))
				rtl_ax_rx_pe_deliver(rx, process);
			process = (process + 1) & 255;
			put_unaligned_le32(process, mailbox + RTL_AX_RX_WORK_PROCESS * 4);
		}
	}
	rtl_ax_rx_work_recycle(rx);
	for (unsigned int i = 0; i < RTL_AX_RX_ENTRIES; i++)
		rx->queues[0].slot_ids[i] = get_unaligned_le32(rx->pe.memory + RTL_AX_RX_SLOT_MAP + i * 4);
	rx->queues[0].consumer = get_unaligned_le32(mailbox + 324);
	rx->queues[0].tag = get_unaligned_le32(mailbox + 328);
	dma_wmb();
	rx->io->write16(rx->io->ctx, 0x1218, rx->queues[0].consumer);
	dma_sync_single_for_cpu(rx->pe_device, rx->queues[1].buffer_dma, RTL_AX_RX_ENTRIES * RTL_AX_RPQ_BUFFER_BYTES, DMA_BIDIRECTIONAL);
	rx->queues[1].consumer = get_unaligned_le32(tx_mailbox + 280);
	rx->queues[1].tag = get_unaligned_le32(tx_mailbox + 284);
	put_unaligned_le32(0, tx_mailbox + 272);
	put_unaligned_le32(0, tx_mailbox + RTL_AX_RX_EVENT_CONTEXT * 4);
	put_unaligned_le32(0, mailbox + 320);
	dma_wmb();
	for (unsigned int i = 0; i < 2; i++)
		rx->dfs_epoch[i] = get_unaligned_le32((i ? tx_mailbox : mailbox) + (RTL_AX_PE_DFS_EPOCH + i) * 4);
	ret = pe_probe_stop(&rx->pe);
	if (ret) return ret;
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
	if (!smp_load_acquire(&rx->rpq_transfer)) {
		if (!READ_ONCE(rx->rpq_owned)) {
			retry = rtl_ax_rx_drain(rx, 0);
			retry |= rtl_ax_rx_drain(rx, 1);
		} else if (rx->rpq_memory) {
			rtl_ax_rx_pe_drain(rx);
			rtl_ax_rx_pe_rxq_drain(rx);
			rtl_ax_rx_uplink_sync(rx);
			rtl_ax_rx_rdu_sync(rx);
			retry = get_unaligned_le32(rx->pe.memory + 0x2178) != get_unaligned_le32(rx->pe.memory + 0x217c);
			retry |= get_unaligned_le32(rx->rpq_memory + 0x2124) != get_unaligned_le32(rx->rpq_memory + 0x2128);
		}
	}
	spin_lock_irqsave(&rx->irq_lock, flags);
	if (rx->running) {
		/* Stock restores RX interrupts after every drain, including an
		 * unready DMA tag. A later RDU must reach the IRQ handler. */
		rx->io->write32(rx->io->ctx, 0x30b0, RTL_AX_IRQ_INDICATION);
		if (retry)
			mod_delayed_work(system_wq, &rx->work, 0);
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
		/* Preserve the first hardware cause before later H2C timeouts. */
		dev_err(&rx->pdev->dev,
			"firmware halt=%08x DMAC_error=%08x host_dispatcher=%08x WDE_error=%08x PLE_error=%08x TX0=%08x HIQ=%08x\n",
			rx->halt_error, io->read32(io->ctx, 0x8524), io->read32(io->ctx, 0x8808),
			io->read32(io->ctx, 0x8c3c), io->read32(io->ctx, 0x903c),
			io->read32(io->ctx, 0x1058), io->read32(io->ctx, 0x107c));
		io->write32(io->ctx, 0x01a4, halt);
	}
	if (pending & BIT(19)) rx->queues[0].unavailable++;
	if (pending & BIT(20)) rx->queues[1].unavailable++;
	rx->interrupts++;
	mod_delayed_work(system_wq, &rx->work, 0);
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	return IRQ_HANDLED;
}

void rtl_ax_rx_log_stall(struct rtl_ax_rx *rx)
{
	const struct rtl_ax_io *io = rx->io;

	dev_err(&rx->pdev->dev,
		"RX state running=%u PE_owned=%u IRQ=%d interrupts=%llu work=%x IMR=%08x ISR=%08x indication=%08x/%08x\n",
		rx->running, rx->rpq_owned, rx->irq, rx->interrupts, work_busy(&rx->work.work),
		io->read32(io->ctx, 0x10b0), io->read32(io->ctx, 0x10b4),
		io->read32(io->ctx, 0x30b0), io->read32(io->ctx, 0x30b4));
	for (unsigned int n = 0; n < 2; n++) {
		struct rtl_ax_rx_queue *q = &rx->queues[n];
		unsigned long flags;
		u32 info, wd;
		spin_lock_irqsave(&rx->irq_lock, flags);
		u8 *buffer = rtl_ax_rx_slot_buffer(rx, n, q->consumer).data;
		info = le32_to_cpu(READ_ONCE(*(__le32 *)buffer));
		wd = le32_to_cpu(READ_ONCE(*(__le32 *)(buffer + 4)));
		spin_unlock_irqrestore(&rx->irq_lock, flags);

		dev_err(&rx->pdev->dev,
			"RX queue=%u consumer=%u tag=%u packets=%llu tag_pending=%llu malformed=%llu next_info_cached=%08x next_WD_cached=%08x\n",
			n, q->consumer, q->tag, q->packets, q->tag_pending, q->malformed,
			info, wd);
	}
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

static int rtl_ax_rx_buffers_alloc(struct rtl_ax_rx *rx)
{
	struct device *device = rx->pe_device;

	for (unsigned int number = 0; number < 2; number++) {
		struct rtl_ax_rx_queue *queue = &rx->queues[number];

		queue->buffer_bytes = number ? RTL_AX_RPQ_BUFFER_BYTES : RTL_AX_RXQ_BUFFER_BYTES;
		queue->descriptors = dma_alloc_coherent(device, RTL_AX_RX_ENTRIES * 8,
			&queue->descriptor_dma, GFP_KERNEL);
		if (!queue->descriptors) return -ENOMEM;
		if (!number) {
			queue->payloads = kcalloc(RTL_AX_RX_BUFFERS, sizeof(*queue->payloads), GFP_KERNEL);
			if (!queue->payloads) return -ENOMEM;
			queue->addresses = dma_alloc_coherent(device, RTL_AX_RX_BUFFERS * sizeof(u32),
				&queue->addresses_dma, GFP_KERNEL);
			if (!queue->addresses) return -ENOMEM;
			for (unsigned int id = 0; id < RTL_AX_RX_BUFFERS; id++) {
				int ret = rtl_ax_rx_buffer_alloc(rx, queue->buffer_bytes,
					GFP_KERNEL, &queue->payloads[id]);
				if (ret) return ret;
				queue->addresses[id] = cpu_to_le32(queue->payloads[id].dma);
				queue->allocated_payloads++;
			}
		} else {
			queue->buffers = dma_alloc_coherent(device, RTL_AX_RX_ENTRIES * queue->buffer_bytes,
				&queue->buffer_dma, GFP_KERNEL);
			if (!queue->buffers) return -ENOMEM;
			memset(queue->buffers, 0, RTL_AX_RX_ENTRIES * queue->buffer_bytes);
		}
		for (unsigned int entry = 0; entry < RTL_AX_RX_ENTRIES; entry++) {
			dma_addr_t dma = number ? queue->buffer_dma + entry * queue->buffer_bytes : queue->payloads[entry].dma;
			queue->slot_ids[entry] = entry;

			rtl_ax_rxbd(queue->descriptors + entry * 8, lower_32_bits(dma), queue->buffer_bytes);
		}
	}
	return 0;
}

static void rtl_ax_rx_buffers_free(struct rtl_ax_rx *rx)
{
	struct device *device = rx->pe_device;

	for (unsigned int number = 0; number < 2; number++) {
		struct rtl_ax_rx_queue *queue = &rx->queues[number];

		for (unsigned int id = 0; id < queue->allocated_payloads; id++) {
			dma_unmap_single(device, queue->payloads[id].dma,
				queue->buffer_bytes, DMA_BIDIRECTIONAL);
			dev_kfree_skb_any(queue->payloads[id].skb);
		}
		kfree(queue->payloads);
		if (queue->addresses)
			dma_free_coherent(device, RTL_AX_RX_BUFFERS * sizeof(u32), queue->addresses, queue->addresses_dma);
		if (queue->buffers)
			dma_free_coherent(device, RTL_AX_RX_ENTRIES * queue->buffer_bytes, queue->buffers, queue->buffer_dma);
		if (queue->descriptors)
			dma_free_coherent(device, RTL_AX_RX_ENTRIES * 8, queue->descriptors, queue->descriptor_dma);
	}
}

void rtl_ax_rx_free(struct rtl_ax_rx *rx)
{
	rtl_ax_rx_release_interrupts(rx);
	pe_probe_free(&rx->pe);
	rtl_ax_rx_buffers_free(rx);
	if (rx->uplink_states) dma_free_coherent(rx->pe_device, SZ_128K, rx->uplink_states, rx->uplink_states_dma);
	if (rx->pe_forward) dma_free_coherent(rx->pe_device, RTL_AX_RX_ENTRIES * RTL_AX_RPQ_BUFFER_BYTES, rx->pe_forward, rx->pe_forward_dma);
	put_device(rx->pe_device);
	kfree(rx);
}

struct rtl_ax_rx *rtl_ax_rx_prepare(struct pci_dev *pdev, const struct rtl_ax_io *io,
				  rtl_ax_rx_handler handler, void *ctx, struct device *rpq_device)
{
	struct rtl_ax_rx *rx;
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
	rx->uplink_states = dma_alloc_coherent(rpq_device, SZ_128K, &rx->uplink_states_dma, GFP_KERNEL);
	if (!rx->uplink_states) { ret = -ENOMEM; goto free; }
	memset(rx->uplink_states, 0, SZ_128K);
	ret = pe_probe_alloc(&rx->pe, pdev->device == 0xc852 ? 0 : 1, 0);
	if (ret) goto free;
	ret = rtl_ax_rx_buffers_alloc(rx);
	if (ret) goto free;

	rx->pe_forward = dma_alloc_coherent(rpq_device, RTL_AX_RX_ENTRIES * RTL_AX_RPQ_BUFFER_BYTES, &rx->pe_forward_dma, GFP_KERNEL);
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

u32 rtl_ax_rx_traffic_bytes(struct rtl_ax_rx *rx)
{
	/* Fast-path bytes bypass the ARM queue. The PE slot retains its counter
	 * across detach/reattach, so include it even while ARM owns RXQ. */
	return (u32)READ_ONCE(rx->queues[0].data_bytes) +
		get_unaligned_le32(rx->pe.memory + 0x2184);
}

int rtl_ax_rx_status(struct rtl_ax_rx *rx, char *out, size_t capacity)
{
	static const char * const encodings[] = { "legacy", "HT", "VHT", "HE" };
	unsigned int type, subtype;
	int length;

	rtl_ax_rx_rdu_sync(rx);
	length = scnprintf(out, capacity,
		"RXQ_owner=%s RPQ_owner=PE1 RXQ_core=0 RX_work_core=0 decoded_type_counters=ARM\nrxq=%llu rpq=%llu rx_malformed=%llu rx_tag_pending=%llu rx_unhandled=%llu "
		"irq=%llu rdu=%llu halt=0x%08x\n",
		rx->rpq_owned ? "PE" : "ARM", rx->queues[0].packets + rx->pe_captured, rx->queues[1].packets,
		rx->queues[0].malformed + rx->queues[1].malformed,
		rx->queues[0].tag_pending + rx->queues[1].tag_pending,
		rx->queues[0].unhandled + rx->queues[1].unhandled,
		rx->interrupts, rx->queues[0].unavailable + rx->queues[1].unavailable + rx->pe_rdu[0] + rx->pe_rdu[1], rx->halt_error);
	length += scnprintf(out + length, capacity - length,
		"RX_skb_transfers=%llu RX_buffer_alloc_failed=%llu RX_skb_prepare_ns=%llu RX_data_bytes=%llu RXQ_RDU=%llu RPQ_RDU=%llu\n",
		rx->queues[0].skb_transfers, rx->queues[0].alloc_failed,
		rx->queues[0].skb_prepare_ns, rx->queues[0].data_bytes,
		rx->queues[0].unavailable + rx->pe_rdu[0], rx->queues[1].unavailable + rx->pe_rdu[1]);
	length += scnprintf(out + length, capacity - length,
		"PE_uplink_frames=%u PE_uplink_bytes=%u ARM_exceptions=%llu ARM_exception_errors=%llu RX_pool=%u NI_pending=%u/%u PE_reordered=%u PE_cross_TID_MPDU=%u\n",
		rx->rpq_memory ? get_unaligned_le32(rx->pe.memory + 0x2180) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->pe.memory + 0x2184) : 0,
		rx->pe_rx_delivered, rx->pe_rx_malformed, RTL_AX_RX_BUFFERS,
		rx->rpq_memory ? get_unaligned_le32(rx->pe.memory + 0x2168) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->pe.memory + 0x216c) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->pe.memory + 0x2000 + 191 * 4) : 0,
		rx->rpq_memory ? get_unaligned_le32(rx->pe.memory + 0x2000 + RTL_AX_RX_CROSS_TID * 4) : 0);
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
