// SPDX-License-Identifier: GPL-2.0-only
/* Native management WP/TXBD ownership and original RPQ completion. */
#include <linux/bitmap.h>
#include <linux/dma-mapping.h>
#include <linux/pci.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <net/mac80211.h>
#include "receive.h"
#include "tx-format.h"
#include "tx.h"

#define RTL_AX_TX_ENTRIES 256
#define RTL_AX_TX_WP_BYTES 128
#define RTL_AX_TX_MGMT_INDEX 0x1078
#define RTL_AX_TX_RINGS 6
#define RTL_AX_TX_STOP_MASK (GENMASK(11, 8) | BIT(16) | BIT(17) | BIT(19))

static const u8 rtl_ax_tx_channels[RTL_AX_TX_RINGS] = { 0, 1, 2, 3, 8, 9 };
static const u16 rtl_ax_tx_indices[RTL_AX_TX_RINGS] = { 0x1058, 0x105c, 0x1060, 0x1064, 0x1078, 0x107c };

struct rtl_ax_tx_slot {
	struct sk_buff *skb;
	dma_addr_t dma;
	unsigned int bytes;
	u8 ring;
};

struct rtl_ax_tx {
	struct pci_dev *pdev;
	struct ieee80211_hw *hw;
	const struct rtl_ax_io *io;
	spinlock_t lock;
	u8 *ring, *pages;
	dma_addr_t ring_dma, pages_dma;
	struct rtl_ax_tx_slot slots[RTL_AX_TX_ENTRIES];
	DECLARE_BITMAP(pending, RTL_AX_TX_ENTRIES);
	unsigned int producer[RTL_AX_TX_RINGS];
	u64 submitted_ring[RTL_AX_TX_RINGS], completed_ring[RTL_AX_TX_RINGS];
	bool running, paused;
	bool queues_stopped;
	unsigned int occupied;
	u64 queue_stops, queue_wakes;
	u64 ampdu_submitted, ampdu_completed;
	u32 scheduler_saved, last_words[14], last_report, last_sequence;
	u64 submitted, completed, status_count[8], polluted, unmatched, stopped, full;
};

static void rtl_ax_tx_wd(u8 *out, unsigned int bytes,
	unsigned int addresses, unsigned int rate, unsigned int gi, unsigned int sequence,
	unsigned int header_bytes, bool no_ack, bool multicast,
	unsigned int channel, unsigned int qsel, bool tid_ind, unsigned int macid,
	unsigned int aggregate, unsigned int density)
{
	/* Actual txdes_proc_mgnt_C@251e14/XB@265014 PCIe2/SCC:
	 * MAC band0,port0,MACID0,raw header,fixed rate,WD-info56.
	 * Original hw-info checksum default0; no private host ABI remains. */
	memset(out, 0, 56);
	/* Actual PHL fill_mdata@1955e8 supplies wp_offset56 and
	 * wd_page_size=wdinfo_en before the MAC descriptor constructor. */
	rtl_ax_store32(out, BIT(7) | ((56U & 31) << 24) | channel << 16 | BIT(22) | ((header_bytes / 2) & 31) << 11 |
		(no_ack ? BIT(31) : 0));
	rtl_ax_store32(out + 4, (addresses & 63) << 26);
	rtl_ax_store32(out + 8, macid << 24 | qsel << 17 | (tid_ind ? BIT(23) : 0) | (bytes & 0x3fff));
	rtl_ax_store32(out + 12, (sequence & 0xfff) | (aggregate ? BIT(12) : 0));
	/* Actual C/XB DATA-WD@2525ac: GI[27:25], rate[24:16], fixed-rate[31]. */
	rtl_ax_store32(out + 28, BIT(31) | (gi & 7) << 25 | (rate & 0x1ff) << 16);
	rtl_ax_store32(out + 32, BIT(9) | BIT(10));
	rtl_ax_store32(out + 36, (multicast ? BIT(11) : 0) | (aggregate ? (aggregate - 1) & 0xff : 0));
	rtl_ax_store32(out + 40, aggregate ? (density & 7) << 18 : 0);
}

static void rtl_ax_tx_checksum(u8 *descriptor)
{
	u16 checksum = 0;
	unsigned int i;

	/* Original checksum@251d2c clears DW4 low16, then XORs every
	 * 16-bit halfword of the complete WD including its 24-byte info. */
	descriptor[16] = descriptor[17] = 0;
	for (i = 0; i < 56; i += 2)
		checksum ^= descriptor[i] | (u16)descriptor[i + 1] << 8;
	descriptor[16] = checksum;
	descriptor[17] = checksum >> 8;
}

struct rtl_ax_tx *rtl_ax_tx_prepare(struct pci_dev *pdev, const struct rtl_ax_io *io)
{
	struct rtl_ax_tx *tx;
	u32 last;
	unsigned int ring;
	int ret;

	tx = kzalloc(sizeof(*tx), GFP_KERNEL);
	if (!tx)
		return ERR_PTR(-ENOMEM);
	tx->pdev = pdev;
	tx->io = io;
	spin_lock_init(&tx->lock);
	tx->ring = dma_alloc_coherent(&pdev->dev, RTL_AX_TX_RINGS * RTL_AX_TX_ENTRIES * 8, &tx->ring_dma, GFP_KERNEL);
	tx->pages = dma_alloc_coherent(&pdev->dev, RTL_AX_TX_ENTRIES * RTL_AX_TX_WP_BYTES,
		&tx->pages_dma, GFP_KERNEL);
	if (!tx->ring || !tx->pages) {
		ret = -ENOMEM;
		goto release;
	}
	memset(tx->ring, 0, RTL_AX_TX_RINGS * RTL_AX_TX_ENTRIES * 8);
	memset(tx->pages, 0, RTL_AX_TX_ENTRIES * RTL_AX_TX_WP_BYTES);
	/* Before the common BDRAM reset in H2C setup, install channel8
	 * from the original C/XB register map and bdram table{40,5,1}. */
	rtl_ax_update32(io, 0x1010, 0, RTL_AX_TX_STOP_MASK);
	ret = io->poll32(io->ctx, 0x101c, RTL_AX_TX_STOP_MASK, 0, 10, 100000, &last);
	if (ret)
		goto release;
	rtl_ax_update32(io, 0x1014, 0, GENMASK(3, 0) | BIT(8) | BIT(9));
	for (ring = 0; ring < RTL_AX_TX_RINGS; ring++) {
		unsigned int channel = rtl_ax_tx_channels[ring];

		ret = io->poll32(io->ctx, rtl_ax_tx_indices[ring], 0x3ff03ff, 0, 10, 100000, &last);
		if (ret)
			goto release;
		io->write32(io->ctx, 0x1230 + channel * 8,
			lower_32_bits(tx->ring_dma + ring * RTL_AX_TX_ENTRIES * 8));
		io->write32(io->ctx, 0x1234 + channel * 8, 0);
		rtl_ax_update16(io, 0x1024 + channel * 2, 0x3ff, RTL_AX_TX_ENTRIES);
		rtl_ax_update32(io, 0x1300 + channel * 4, 0x3f3f3f,
			channel * 5 | 5U << 8 | (channel < 8 ? 2U : 1U) << 16);
	}
	return tx;
release:
	rtl_ax_tx_free(tx, NULL);
	return ERR_PTR(ret);
}

int rtl_ax_tx_pause(struct rtl_ax_tx *tx)
{
	const struct rtl_ax_io *io = tx->io;
	unsigned long flags;
	unsigned int i;

	spin_lock_irqsave(&tx->lock, flags);
	tx->running = false;
	rtl_ax_update32(io, 0x1010, 0, RTL_AX_TX_STOP_MASK);
	if (!tx->paused) {
		tx->scheduler_saved = io->read32(io->ctx, 0xc398) & 0x3ffff;
		tx->paused = true;
	}
	/* Actual set_hw_sch_tx_en C/XB writes C398, 18 queue bits.
	 * RFK reason2 additionally waits for band0 TX idle at C6ec[7]. */
	rtl_ax_update32(io, 0xc398, 0x3ffff, 0);
	spin_unlock_irqrestore(&tx->lock, flags);
	for (i = 0; i < 2199; i++) {
		if (!(io->read8(io->ctx, 0xc6ec) & BIT(7)))
			return 0;
		io->delay_us(io->ctx, 30);
	}
	return -ETIMEDOUT;
}

void rtl_ax_tx_open(struct rtl_ax_tx *tx)
{
	const struct rtl_ax_io *io = tx->io;
	unsigned long flags;

	spin_lock_irqsave(&tx->lock, flags);
	rtl_ax_update32(io, 0xc398, 0x3ffff, tx->scheduler_saved | BIT(8));
	rtl_ax_update32(io, 0x1010, RTL_AX_TX_STOP_MASK, 0);
	tx->paused = false;
	tx->running = true;
	if (tx->queues_stopped && tx->occupied <= RTL_AX_TX_ENTRIES / 2) {
		tx->queues_stopped = false;
		tx->queue_wakes++;
		ieee80211_wake_queues(tx->hw);
	}
	spin_unlock_irqrestore(&tx->lock, flags);
}

int rtl_ax_tx_submit(struct rtl_ax_tx *tx, struct ieee80211_hw *hw,
	struct sk_buff *skb, unsigned int rate, unsigned int gi, unsigned int macid,
	unsigned int aggregate, unsigned int density)
{
	struct ieee80211_tx_info *info = IEEE80211_SKB_CB(skb);
	struct ieee80211_hdr *header = (void *)skb->data;
	static const u8 tid_indicators[] = { 0, 0, 1, 1, 0, 1, 0, 1 };
	unsigned int id, consumer, next, addresses, length, ring, tid = 0, qsel;
	unsigned long flags;
	dma_addr_t dma;
	u8 *page;
	int ret;

	ret = skb_linearize(skb);
	if (ret)
		return ret;
	header = (void *)skb->data;
	if (ieee80211_is_mgmt(header->frame_control)) {
		ring = 4;
		qsel = 0x12;
	} else if (info->flags & IEEE80211_TX_CTL_SEND_AFTER_DTIM) {
		ring = 5;
		qsel = 0x11;
	} else {
		if (ieee80211_is_data_qos(header->frame_control))
			tid = *ieee80211_get_qos_ctl(header) & 7;
		ring = rtl_ax_tx_dma_channel(tid, 0);
		qsel = ring;
	}
	dma = dma_map_single(&tx->pdev->dev, skb->data, skb->len, DMA_TO_DEVICE);
	if (dma_mapping_error(&tx->pdev->dev, dma))
		return -ENOMEM;
	spin_lock_irqsave(&tx->lock, flags);
	if (!tx->running) {
		tx->stopped++;
		ret = -ENETDOWN;
		goto unmap;
	}
	id = find_first_zero_bit(tx->pending, RTL_AX_TX_ENTRIES);
	consumer = (tx->io->read32(tx->io->ctx, rtl_ax_tx_indices[ring]) >> 16) & 0x3ff;
	next = (tx->producer[ring] + 1) & (RTL_AX_TX_ENTRIES - 1);
	if (id == RTL_AX_TX_ENTRIES || next == consumer) {
		tx->full++;
		ret = -ENOSPC;
		goto unmap;
	}
	page = tx->pages + id * RTL_AX_TX_WP_BYTES;
	memset(page, 0, RTL_AX_TX_WP_BYTES);
	addresses = rtl_ax_tx_wp_addresses(page + 56 + RTL_AX_TX_WP_SEQUENCE_BYTES, lower_32_bits(dma), skb->len);
	length = 56 + RTL_AX_TX_WP_SEQUENCE_BYTES + addresses * RTL_AX_TX_WP_ADDRESS_BYTES;
	rtl_ax_tx_wd(page, skb->len, addresses, rate, gi,
		le16_to_cpu(header->seq_ctrl) >> 4, ieee80211_hdrlen(header->frame_control),
		info->flags & IEEE80211_TX_CTL_NO_ACK, is_multicast_ether_addr(header->addr1),
		rtl_ax_tx_channels[ring], qsel, tid_indicators[tid], macid, aggregate, density);
	if (tx->io->read32(tx->io->ctx, 0x8830) & BIT(0))
		rtl_ax_tx_checksum(page);
	rtl_ax_tx_wp_sequence(page + 56, id | (tid_indicators[tid] ? BIT(14) : 0));
	tx->slots[id] = (struct rtl_ax_tx_slot) { .skb = skb, .dma = dma, .bytes = skb->len, .ring = ring };
	__set_bit(id, tx->pending);
	tx->hw = hw;
	tx->occupied++;
	rtl_ax_txbd(tx->ring + (ring * RTL_AX_TX_ENTRIES + tx->producer[ring]) * 8,
		lower_32_bits(tx->pages_dma + id * RTL_AX_TX_WP_BYTES), length);
	dma_wmb();
	tx->producer[ring] = next;
	tx->submitted++;
	tx->ampdu_submitted += !!aggregate;
	tx->submitted_ring[ring]++;
	tx->last_sequence = id;
	memcpy(tx->last_words, page, sizeof(tx->last_words));
	tx->io->write16(tx->io->ctx, rtl_ax_tx_indices[ring], next);
	/* WP IDs are shared by all six DMA rings. Pause mac80211 dequeue
	 * before exhausting them; RPQ release owns the matching wake. */
	if (tx->occupied >= RTL_AX_TX_ENTRIES - 32 && !tx->queues_stopped) {
		tx->queues_stopped = true;
		tx->queue_stops++;
		ieee80211_stop_queues(hw);
	}
	spin_unlock_irqrestore(&tx->lock, flags);
	return 0;
unmap:
	spin_unlock_irqrestore(&tx->lock, flags);
	dma_unmap_single(&tx->pdev->dev, dma, skb->len, DMA_TO_DEVICE);
	return ret;
}

int rtl_ax_tx_report(struct rtl_ax_tx *tx, struct ieee80211_hw *hw, const struct rtl_ax_rx_packet *packet)
{
	unsigned int offset;
	unsigned long flags;

	/* Original handle_rx_report@27c1a4: four-byte release records,
	 * qsel8:12, status13:15, WPseq16:30 (ID low12), polluted31. */
	for (offset = 0; offset + 4 <= packet->bytes; offset += 4) {
		u32 report = rtl_ax_ap_read32(packet->payload + offset);
		unsigned int id = (report >> 16) & 0xfff, status = (report >> 13) & 7;
		unsigned int qsel = (report >> 8) & 31;
		unsigned int ring = qsel < 4 ? qsel : qsel == 0x12 ? 4 : qsel == 0x11 ? 5 : RTL_AX_TX_RINGS;
		struct rtl_ax_tx_slot slot;
		struct ieee80211_tx_info *info;

		spin_lock_irqsave(&tx->lock, flags);
		tx->last_report = report;
		if (id >= RTL_AX_TX_ENTRIES || !test_bit(id, tx->pending) || tx->slots[id].ring != ring) {
			tx->unmatched++;
			spin_unlock_irqrestore(&tx->lock, flags);
			continue;
		}
		slot = tx->slots[id];
		tx->slots[id].skb = NULL;
		__clear_bit(id, tx->pending);
		tx->occupied--;
		tx->completed++;
		tx->completed_ring[ring]++;
		tx->status_count[status]++;
		tx->polluted += !!(report & BIT(31));
		if (tx->queues_stopped && tx->running && tx->occupied <= RTL_AX_TX_ENTRIES / 2) {
			tx->queues_stopped = false;
			tx->queue_wakes++;
			ieee80211_wake_queues(hw);
		}
		spin_unlock_irqrestore(&tx->lock, flags);
		dma_unmap_single(&tx->pdev->dev, slot.dma, slot.bytes, DMA_TO_DEVICE);
		info = IEEE80211_SKB_CB(slot.skb);
		ieee80211_tx_info_clear_status(info);
		if (!status && !(report & BIT(31)))
			info->flags |= info->flags & IEEE80211_TX_CTL_NO_ACK ?
				IEEE80211_TX_STAT_NOACK_TRANSMITTED : IEEE80211_TX_STAT_ACK;
		info->status.rates[0].count = 1;
		if (info->flags & IEEE80211_TX_CTL_AMPDU) {
			/* RPQ returns one WP/MPDU at a time, not a PPDU aggregate count.
			 * Account the actual completed MPDU and its individual result. */
			info->flags |= IEEE80211_TX_STAT_AMPDU;
			info->status.ampdu_len = 1;
			info->status.ampdu_ack_len = !!(info->flags & IEEE80211_TX_STAT_ACK);
			spin_lock_irqsave(&tx->lock, flags);
			tx->ampdu_completed++;
			spin_unlock_irqrestore(&tx->lock, flags);
		}
		/* RPQ is dispatched by the same RX worker as received frames.
		 * Pair its process-context delivery with ieee80211_rx_ni. */
		ieee80211_tx_status_ni(hw, slot.skb);
	}
	return 0;
}

void rtl_ax_tx_free(struct rtl_ax_tx *tx, struct ieee80211_hw *hw)
{
	unsigned int i;

	if (!tx)
		return;
	/* Caller has closed RX work and removed device DMA ownership. */
	for (i = 0; i < RTL_AX_TX_ENTRIES; i++)
		if (tx->slots[i].skb) {
			dma_unmap_single(&tx->pdev->dev, tx->slots[i].dma, tx->slots[i].bytes, DMA_TO_DEVICE);
			ieee80211_free_txskb(hw, tx->slots[i].skb);
		}
	if (tx->pages)
		dma_free_coherent(&tx->pdev->dev, RTL_AX_TX_ENTRIES * RTL_AX_TX_WP_BYTES, tx->pages, tx->pages_dma);
	if (tx->ring)
		dma_free_coherent(&tx->pdev->dev, RTL_AX_TX_RINGS * RTL_AX_TX_ENTRIES * 8, tx->ring, tx->ring_dma);
	kfree(tx);
}

int rtl_ax_tx_status(struct rtl_ax_tx *tx, char *out, size_t capacity)
{
	unsigned long flags;
	unsigned int i;
	int length;

	spin_lock_irqsave(&tx->lock, flags);
	length = scnprintf(out, capacity, "native_TX_running=%u submitted=%llu completed=%llu pending=%u "
		"last_sequence=%u last_RPQ=%08x polluted=%llu unmatched=%llu full=%llu stopped=%llu "
		"scheduler=%08x DMA_stop=%08x WD_checksum=%08x\n",
		tx->running, tx->submitted, tx->completed, bitmap_weight(tx->pending, RTL_AX_TX_ENTRIES),
		tx->last_sequence, tx->last_report, tx->polluted, tx->unmatched, tx->full, tx->stopped,
		tx->io->read32(tx->io->ctx, 0xc398), tx->io->read32(tx->io->ctx, 0x1010), tx->io->read32(tx->io->ctx, 0x8830));
	for (i = 0; i < RTL_AX_TX_RINGS; i++)
		length += scnprintf(out + length, capacity - length, "DMA_channel=%u submitted=%llu completed=%llu producer=%u hardware=%u\n",
			rtl_ax_tx_channels[i], tx->submitted_ring[i], tx->completed_ring[i], tx->producer[i],
			(tx->io->read32(tx->io->ctx, rtl_ax_tx_indices[i]) >> 16) & 0x3ff);
	length += scnprintf(out + length, capacity - length,
		"queues_stopped=%u queue_stops=%llu queue_wakes=%llu\n",
		tx->queues_stopped, tx->queue_stops, tx->queue_wakes);
	length += scnprintf(out + length, capacity - length, "AMPDU_submitted_MPDU=%llu AMPDU_completed_MPDU=%llu\n",
		tx->ampdu_submitted, tx->ampdu_completed);
	for (i = 0; i < 8; i++)
		length += scnprintf(out + length, capacity - length, "TX_status=%u count=%llu\n", i, tx->status_count[i]);
	for (i = 0; i < 14; i++)
		length += scnprintf(out + length, capacity - length, "WD_word=%u value=%08x\n", i, tx->last_words[i]);
	spin_unlock_irqrestore(&tx->lock, flags);
	return length;
}
