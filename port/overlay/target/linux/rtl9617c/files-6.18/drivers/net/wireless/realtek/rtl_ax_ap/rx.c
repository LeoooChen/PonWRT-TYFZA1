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

struct rtl_ax_rx {
	struct pci_dev *pdev;
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
};

static struct rtl_ax_rx_buffer rtl_ax_rx_slot_buffer(struct rtl_ax_rx *rx,
	unsigned int number, unsigned int slot)
{
	struct rtl_ax_rx_queue *queue = &rx->queues[number];

	if (number)
		return (struct rtl_ax_rx_buffer){queue->buffers + slot * queue->buffer_bytes,
			queue->buffer_dma + slot * queue->buffer_bytes};
	return queue->payloads[slot];
}

/* RX work, PE and PCI DMA are paused. Mark each ring's old prefix, using
 * the PE scan cursor rather than the RXBD return cursor for owned queues. */
void rtl_ax_rx_dfs_epoch(struct rtl_ax_rx *rx)
{
	for (unsigned int number = 0; number < 2; number++) {
		unsigned int consumer = rx->queues[number].consumer;
		unsigned int producer = (rx->io->read32(rx->io->ctx, 0x1218 + number * 4) >> 16) & 0x3ff;

		if (number && rx->rpq_owned)
			consumer = get_unaligned_le32(rx->rpq_memory + 0x2118);
		rx->dfs_epoch[number] = producer | (consumer != producer ? 256 : 0);
		if (number && rx->rpq_owned) {
			u8 *mailbox = rx->rpq_memory + 0x2000;
			u32 read = get_unaligned_le32(mailbox + 296);
			u32 write = get_unaligned_le32(mailbox + 292);
			put_unaligned_le32(rx->dfs_epoch[1], mailbox + (RTL_AX_PE_DFS_EPOCH + 1) * 4);
			rx->dfs_epoch[3] = write | (read != write ? 256 : 0);
		}
	}
	dma_wmb();
}

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
	struct rtl_ax_rx_buffer old = q->payloads[q->consumer], replacement;
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
	q->payloads[q->consumer] = replacement;
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

void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx)
{
	if (READ_ONCE(rx->running)) mod_delayed_work(system_wq, &rx->work, 0);
}

void rtl_ax_rx_pe_rpq_start(struct rtl_ax_rx *rx, void *memory)
{
	u8 *mailbox = memory + 0x2000;
	unsigned long flags;

	spin_lock_irqsave(&rx->irq_lock, flags);
	rx->rpq_transfer = true;
	rx->irq_mask = 0;
	rx->io->write32(rx->io->ctx, 0x10b0, 0);
	spin_unlock_irqrestore(&rx->irq_lock, flags);
	synchronize_irq(rx->irq);
	cancel_delayed_work_sync(&rx->work);
	dma_sync_single_for_device(rx->pe_device, rx->queues[1].buffer_dma,
		RTL_AX_RX_ENTRIES * RTL_AX_RPQ_BUFFER_BYTES, DMA_BIDIRECTIONAL);
	rx->rpq_memory = memory;
	rx->rpq_owned = true;
	rx->dfs_epoch[3] = 0;
	put_unaligned_le32(rx->dfs_epoch[1], mailbox + (RTL_AX_PE_DFS_EPOCH + 1) * 4);
	put_unaligned_le32(rx->queues[1].consumer, mailbox + 280);
	put_unaligned_le32(rx->queues[1].tag, mailbox + 284);
	put_unaligned_le32(rx->pe_forward_dma, mailbox + 288);
	put_unaligned_le32(0, mailbox + 292);
	put_unaligned_le32(0, mailbox + 296);
	/* PE owns only RPQ. RXQ indices, buffers and PN/SN stay with ARM. */
	put_unaligned_le32(0, mailbox + 320);
	dma_wmb();
	put_unaligned_le32(rx->queues[1].buffer_dma, mailbox + 272);
	/* ARM must still acknowledge RPQ RDU, not just RXQ interrupts. */
	rx->irq_mask = RTL_AX_RX_IRQS & ~BIT(2);
	rx->io->write32(rx->io->ctx, 0x10b0, rx->irq_mask);
	smp_store_release(&rx->rpq_transfer, false);
	rx->io->write32(rx->io->ctx, 0x30b0, RTL_AX_IRQ_INDICATION);
	rtl_ax_rx_pe_wake(rx);
}

int rtl_ax_rx_pe_rpq_stop(struct rtl_ax_rx *rx)
{
	u8 *mailbox;
	unsigned long flags;

	if (!rx->rpq_memory) return 0;
	/* Caller paused PE; RXQ continues from its own ARM cursor after this. */
	WRITE_ONCE(rx->rpq_transfer, true);
	cancel_delayed_work_sync(&rx->work);
	mailbox = rx->rpq_memory + 0x2000;
	do { rtl_ax_rx_pe_drain(rx); }
	while (get_unaligned_le32(mailbox + 296) != get_unaligned_le32(mailbox + 292));
	dma_sync_single_for_cpu(rx->pe_device, rx->queues[1].buffer_dma,
		RTL_AX_RX_ENTRIES * RTL_AX_RPQ_BUFFER_BYTES, DMA_BIDIRECTIONAL);
	rx->queues[1].consumer = get_unaligned_le32(mailbox + 280);
	rx->queues[1].tag = get_unaligned_le32(mailbox + 284);
	rx->dfs_epoch[1] = get_unaligned_le32(mailbox + (RTL_AX_PE_DFS_EPOCH + 1) * 4);
	put_unaligned_le32(0, mailbox + 272);
	dma_wmb();
	rx->rpq_memory = NULL;
	spin_lock_irqsave(&rx->irq_lock, flags);
	rx->rpq_owned = false;
	rx->irq_mask = RTL_AX_RX_IRQS;
	if (rx->running) rx->io->write32(rx->io->ctx, 0x10b0, rx->irq_mask);
	smp_store_release(&rx->rpq_transfer, false);
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
			retry = rtl_ax_rx_drain(rx, 0);
			retry |= get_unaligned_le32(rx->rpq_memory + 0x2124) != get_unaligned_le32(rx->rpq_memory + 0x2128);
		}
	}
	spin_lock_irqsave(&rx->irq_lock, flags);
	if (rx->running) {
		if (retry)
			mod_delayed_work(system_wq, &rx->work, 0);
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
			queue->payloads = kcalloc(RTL_AX_RX_ENTRIES, sizeof(*queue->payloads), GFP_KERNEL);
			if (!queue->payloads) return -ENOMEM;
			for (unsigned int id = 0; id < RTL_AX_RX_ENTRIES; id++) {
				int ret = rtl_ax_rx_buffer_alloc(rx, queue->buffer_bytes,
					GFP_KERNEL, &queue->payloads[id]);
				if (ret) return ret;
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
		if (queue->buffers)
			dma_free_coherent(device, RTL_AX_RX_ENTRIES * queue->buffer_bytes, queue->buffers, queue->buffer_dma);
		if (queue->descriptors)
			dma_free_coherent(device, RTL_AX_RX_ENTRIES * 8, queue->descriptors, queue->descriptor_dma);
	}
}

void rtl_ax_rx_free(struct rtl_ax_rx *rx)
{
	rtl_ax_rx_release_interrupts(rx);
	rtl_ax_rx_buffers_free(rx);
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
	INIT_DELAYED_WORK(&rx->work, rtl_ax_rx_work);
	io->write32(io->ctx, 0x30b0, 0);
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
	return (u32)READ_ONCE(rx->queues[0].data_bytes);
}

int rtl_ax_rx_status(struct rtl_ax_rx *rx, char *out, size_t capacity)
{
	static const char * const encodings[] = { "legacy", "HT", "VHT", "HE" };
	unsigned int type, subtype;
	int length;

	length = scnprintf(out, capacity,
		"RXQ_owner=ARM RPQ_owner=PE_when_enabled\nrxq=%llu rpq=%llu rx_malformed=%llu rx_tag_pending=%llu rx_unhandled=%llu "
		"irq=%llu rdu=%llu halt=0x%08x\n",
		rx->queues[0].packets, rx->queues[1].packets,
		rx->queues[0].malformed + rx->queues[1].malformed,
		rx->queues[0].tag_pending + rx->queues[1].tag_pending,
		rx->queues[0].unhandled + rx->queues[1].unhandled,
		rx->interrupts, rx->queues[0].unavailable + rx->queues[1].unavailable, rx->halt_error);
	length += scnprintf(out + length, capacity - length,
		"RX_skb_transfers=%llu RX_buffer_alloc_failed=%llu RX_skb_prepare_ns=%llu RX_data_bytes=%llu RXQ_RDU=%llu RPQ_RDU=%llu\n",
		rx->queues[0].skb_transfers, rx->queues[0].alloc_failed,
		rx->queues[0].skb_prepare_ns, rx->queues[0].data_bytes,
		rx->queues[0].unavailable, rx->queues[1].unavailable);
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
