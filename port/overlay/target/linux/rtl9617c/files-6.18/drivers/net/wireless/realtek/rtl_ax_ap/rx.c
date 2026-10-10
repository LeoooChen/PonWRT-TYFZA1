// SPDX-License-Identifier: GPL-2.0-only
/* Native RXQ/RPQ ownership and packet dispatch,following the board's
 * hal_check_rxrdy/hal_handle_rxbd_info/update_rxbd/notify_rxdone path. */
#include <linux/dma-mapping.h>
#include <linux/crc32.h>
#include <linux/interrupt.h>
#include <linux/pci.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
#include "receive.h"
#include "rx.h"

#define RTL_AX_RX_IRQS (BIT(20) | BIT(19) | BIT(2) | BIT(0))
#define RTL_AX_IRQ_INDICATION (BIT(29) | BIT(24))

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

struct rtl_ax_rx {
	struct pci_dev *pdev;
	const struct rtl_ax_io *io;
	struct rtl_ax_rx_queue queues[2];
	rtl_ax_rx_handler handler;
	void *handler_ctx;
	struct delayed_work work;
	spinlock_t irq_lock;
	bool running, irq_requested, vectors;
	int irq;
	u64 interrupts, unavailable;
	u32 halt_error;
};

static bool rtl_ax_rx_drain(struct rtl_ax_rx *rx, unsigned int number)
{
	struct rtl_ax_rx_queue *queue = &rx->queues[number];
	const struct rtl_ax_io *io = rx->io;
	unsigned int count, index = 0x1218 + number * 4;

	for (count = 0; count < 128; count++) {
		struct rtl_ax_rx_packet packet;
		unsigned int hardware = (io->read32(io->ctx, index) >> 16) & 0x3ff;
		unsigned int expected = rtl_ax_rx_next_tag(queue->tag), attempt, info;
		u8 *buffer;
		int ret;

		if (hardware == queue->consumer)
			return false;
		if (hardware >= RTL_AX_RX_ENTRIES) {
			queue->malformed++;
			return false;
		}
		buffer = queue->buffers + queue->consumer * queue->buffer_bytes;
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
			    lower_32_bits(queue->buffer_dma + queue->consumer * queue->buffer_bytes),
			    queue->buffer_bytes);
		dma_wmb();
		queue->consumer = (queue->consumer + 1) % RTL_AX_RX_ENTRIES;
		io->write16(io->ctx, index, queue->consumer);
	}
	return true;
}

static void rtl_ax_rx_work(struct work_struct *work)
{
	struct rtl_ax_rx *rx = container_of(to_delayed_work(work), struct rtl_ax_rx, work);
	unsigned long flags;
	bool retry;

	if (!READ_ONCE(rx->running))
		return;
	retry = rtl_ax_rx_drain(rx, 0);
	retry |= rtl_ax_rx_drain(rx, 1);
	spin_lock_irqsave(&rx->irq_lock, flags);
	if (rx->running) {
		if (retry)
			mod_delayed_work(system_wq, &rx->work, msecs_to_jiffies(1));
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
	pending = indication & BIT(29) ? io->read32(io->ctx, 0x10b4) & RTL_AX_RX_IRQS : 0;
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
	io->write32(io->ctx, 0x10b0, RTL_AX_RX_IRQS);
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

void rtl_ax_rx_free(struct rtl_ax_rx *rx)
{
	unsigned int number;

	if (rx->irq_requested)
		free_irq(rx->irq, rx);
	if (rx->vectors)
		pci_free_irq_vectors(rx->pdev);
	for (number = 0; number < 2; number++) {
		struct rtl_ax_rx_queue *queue = &rx->queues[number];

		if (queue->buffers)
			dma_free_coherent(&rx->pdev->dev, RTL_AX_RX_ENTRIES * queue->buffer_bytes,
					  queue->buffers, queue->buffer_dma);
		if (queue->descriptors)
			dma_free_coherent(&rx->pdev->dev, RTL_AX_RX_ENTRIES * 8,
					  queue->descriptors, queue->descriptor_dma);
	}
	kfree(rx);
}

struct rtl_ax_rx *rtl_ax_rx_prepare(struct pci_dev *pdev, const struct rtl_ax_io *io,
				  rtl_ax_rx_handler handler, void *ctx)
{
	struct rtl_ax_rx *rx;
	unsigned int number, entry;
	int ret;

	rx = kzalloc(sizeof(*rx), GFP_KERNEL);
	if (!rx)
		return ERR_PTR(-ENOMEM);
	rx->pdev = pdev;
	rx->io = io;
	rx->handler = handler;
	rx->handler_ctx = ctx;
	spin_lock_init(&rx->irq_lock);
	INIT_DELAYED_WORK(&rx->work, rtl_ax_rx_work);
	io->write32(io->ctx, 0x30b0, 0);
	for (number = 0; number < 2; number++) {
		struct rtl_ax_rx_queue *queue = &rx->queues[number];

		queue->buffer_bytes = number ? RTL_AX_RPQ_BUFFER_BYTES : RTL_AX_RXQ_BUFFER_BYTES;
		queue->descriptors = dma_alloc_coherent(&pdev->dev, RTL_AX_RX_ENTRIES * 8,
						      &queue->descriptor_dma, GFP_KERNEL);
		queue->buffers = dma_alloc_coherent(&pdev->dev, RTL_AX_RX_ENTRIES * queue->buffer_bytes,
						  &queue->buffer_dma, GFP_KERNEL);
		if (!queue->descriptors || !queue->buffers) {
			ret = -ENOMEM;
			goto free;
		}
		memset(queue->buffers, 0, RTL_AX_RX_ENTRIES * queue->buffer_bytes);
		for (entry = 0; entry < RTL_AX_RX_ENTRIES; entry++)
			rtl_ax_rxbd(queue->descriptors + entry * 8,
				    lower_32_bits(queue->buffer_dma + entry * queue->buffer_bytes),
				    queue->buffer_bytes);
	}
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
