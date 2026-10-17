// SPDX-License-Identifier: GPL-2.0-only
/* Native management WP/TXBD ownership and original RPQ completion. */
#include <linux/bitmap.h>
#include <linux/dma-mapping.h>
#include <linux/pci.h>
#include <linux/scatterlist.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/wait.h>
#include <net/mac80211.h>
#include <linux/rtl9617c-wifi.h>
#include "receive.h"
#include "tx-format.h"
#include "security.h"
#include "tx.h"
#include "rx.h"
#include "pe_probe.h"
#include "pe-tx-code.h"
#include "ni-state.h"

#define RTL_AX_TX_ENTRIES 256
#define RTL_AX_TX_PAGES 384
static int ni_test_remove(struct rtl_ax_tx *tx);
static void pe_event_release(struct rtl_ax_tx *tx);
static void ni_route_sleep(struct rtl_ax_tx *tx, unsigned int macid);
static void ni_state_init(struct rtl_ax_tx *tx);
static u64 pe_event_interrupts;
#define RTL_AX_TX_MGMT_INDEX 0x1078
#define RTL_AX_TX_RINGS 6
#define RTL_AX_TX_STOP_MASK (GENMASK(11, 8) | BIT(16) | BIT(17))

static const u8 rtl_ax_tx_channels[RTL_AX_TX_RINGS] = { 0, 1, 2, 3, 8, 9 };
static const u16 rtl_ax_tx_indices[RTL_AX_TX_RINGS] = { 0x1058, 0x105c, 0x1060, 0x1064, 0x1078, 0x107c };

struct rtl_ax_tx_mapping {
	struct scatterlist sg[RTL_AX_TX_WP_ADDRESS_MAX];
	unsigned int count, dma_count;
};

struct rtl_ax_tx_slot {
	struct sk_buff *skb;
	dma_addr_t dma;
	unsigned int bytes;
	u8 ring;
	u8 key_index;
	u8 macid, tid;
	bool encrypted, pe_sequence;
	struct rtl_ax_tx_mapping *mapping;
};

struct rtl_ax_tx {
	struct pci_dev *pdev;
	struct device *dma_device;
	struct pe_probe pe;
	struct rtl_ax_ni_state ni;
	bool pe_active;
	u32 pe_command;
	unsigned int pe_producer;
	struct ieee80211_hw *hw;
	struct rtl_ax_rx *rx;
	void (*peer_ready)(void *context);
	void *peer_ready_context;
	bool peer_offloaded[128], peer_authorized[128];
	const struct rtl_ax_io *io;
	spinlock_t lock;
	wait_queue_head_t key_flush_wait;
	u8 *ring, *pages, *headers;
	bool peer_qos[128];
	bool peer_asleep[128];
	u32 peer_rates[128];
	u64 peer_sleep_events, peer_wake_events;
	u64 ps_releases, ps_eosp;
	u64 pe_ethernet;
	u64 ni_reports, ni_failed;
	u64 ni_first_report_ns;
	unsigned int ni_rpq_producer;
	u32 last_eth_dma;
	u8 last_eth[14];
	dma_addr_t ring_dma, pages_dma, headers_dma;
	struct rtl_ax_tx_slot slots[RTL_AX_TX_ENTRIES];
	DECLARE_BITMAP(pending, RTL_AX_TX_ENTRIES);
	unsigned int producer[RTL_AX_TX_RINGS];
	u64 submitted_ring[RTL_AX_TX_RINGS], completed_ring[RTL_AX_TX_RINGS];
	bool running, paused, dfs_blocked;
	bool checksum_enabled;
	bool queues_stopped, queues_paused;
	unsigned int occupied;
	u64 queue_stops, queue_wakes;
	u64 ampdu_submitted, ampdu_completed;
	u32 scheduler_saved, last_words[14], last_report, last_sequence;
	u64 submitted, completed, status_count[8], polluted, unmatched, stopped, full;
	u64 encrypted, hardware_pn;
	u64 prepare_ns;
	u64 fast_xmit;
	u64 scatter_packets, scatter_segments, amsdu_packets, linearized_packets;
};

struct device *rtl_ax_tx_dma_device(struct rtl_ax_tx *tx) { return tx->dma_device; }
void rtl_ax_tx_bind_rx(struct rtl_ax_tx *tx, struct rtl_ax_rx *rx) { tx->rx = rx; }
void rtl_ax_tx_bind_hw(struct rtl_ax_tx *tx, struct ieee80211_hw *hw)
{
	unsigned long flags;

	spin_lock_irqsave(&tx->lock, flags);
	tx->hw = hw;
	spin_unlock_irqrestore(&tx->lock, flags);
}
void rtl_ax_tx_bind_ready(struct rtl_ax_tx *tx, void (*ready)(void *), void *context)
{
	tx->peer_ready = ready;
	tx->peer_ready_context = context;
}

static u64 ni_reports(struct rtl_ax_tx *tx)
{
	return tx->ni_reports + get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 300);
}
static u64 ni_failed(struct rtl_ax_tx *tx)
{
	return tx->ni_failed + get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 304);
}

static void rtl_ax_tx_wd(u8 *out, unsigned int bytes,
	unsigned int addresses, unsigned int rate, unsigned int gi, unsigned int bandwidth, unsigned int sequence,
	unsigned int header_bytes, bool no_ack, bool multicast,
	unsigned int channel, unsigned int qsel, bool tid_ind, unsigned int macid,
	unsigned int aggregate, unsigned int density)
{
	/* Actual txdes_proc_mgnt_C@251e14/XB@265014 PCIe2/SCC:
	 * MAC band0,port0,MACID0,raw header,fixed rate,WD-info56.
	 * Original hw-info checksum default0; no private host ABI remains. */
	memset(out, 0, 56);
	/* WP_OFFSET reserves inserted headers in8-byte units, not WD bytes.
	 * Native raw WLAN frames need no reservation until hardware adds IV. */
	rtl_ax_store32(out, BIT(7) | channel << 16 | BIT(22) | ((header_bytes / 2) & 31) << 11 |
		(no_ack ? BIT(31) : 0));
	rtl_ax_store32(out + 4, (addresses & 63) << 26);
	rtl_ax_store32(out + 8, macid << 24 | qsel << 17 | (tid_ind ? BIT(23) : 0) | (bytes & 0x3fff));
	rtl_ax_store32(out + 12, (sequence & 0xfff) | (aggregate ? BIT(12) : 0));
	/* Actual C/XB DATA-WD@2525ac: GI[27:25], rate[24:16], fixed-rate[31]. */
	rtl_ax_store32(out + 28, BIT(31) | (bandwidth & 3) << 28 | (gi & 7) << 25 | (rate & 0x1ff) << 16);
	rtl_ax_store32(out + 32, BIT(9) | BIT(10) | (rate & BIT(22) ? BIT(12) : 0));
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

static int rtl_ax_tx_pe_start(struct rtl_ax_tx *tx)
{
	u32 status;
	int ret;
	put_unaligned_le32(tx->pe.slot ? 0xb2000000 : 0xb1000000, tx->pe.memory + PE_PROBE_MAILBOX + 8);
	put_unaligned_le32(tx->ring_dma, tx->pe.memory + PE_PROBE_MAILBOX + 44);
	put_unaligned_le32(tx->pages_dma, tx->pe.memory + PE_PROBE_MAILBOX + 48);
	put_unaligned_le32(tx->headers_dma, tx->pe.memory + PE_PROBE_MAILBOX + 56);
	dma_wmb();
	ret = pe_probe_start(&tx->pe, pe_tx_program, sizeof(pe_tx_program));
	if (ret) return ret;
	ret = read_poll_timeout(get_unaligned_le32, status, status == 0x50455744,
		10, 100000, false, tx->pe.memory + PE_PROBE_MAILBOX);
	if (!ret) tx->pe_active = true;
	return ret;
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
	tx->dma_device = &pdev->dev;
	init_waitqueue_head(&tx->key_flush_wait);
	ni_state_init(tx);
	tx->io = io;
	spin_lock_init(&tx->lock);
	{
		ret = pe_probe_alloc(&tx->pe, pdev->device == 0xc852 ? 0 : 1);
		if (ret) goto release;
		/* PCIe snoops ARM caches; PE reads DDR without that snoop path. */
		tx->dma_device = &tx->pe.device->dev;
	}
	tx->ring = dma_alloc_coherent(tx->dma_device, RTL_AX_TX_RINGS * RTL_AX_TX_ENTRIES * 8, &tx->ring_dma, GFP_KERNEL);
	tx->pages = dma_alloc_coherent(tx->dma_device, RTL_AX_TX_PAGES * RTL_AX_TX_WP_BYTES,
		&tx->pages_dma, GFP_KERNEL);
	tx->headers = dma_alloc_coherent(tx->dma_device, RTL_AX_TX_PAGES * 128, &tx->headers_dma, GFP_KERNEL);
	if (!tx->ring || !tx->pages || !tx->headers) {
		ret = -ENOMEM;
		goto release;
	}
	memset(tx->ring, 0, RTL_AX_TX_RINGS * RTL_AX_TX_ENTRIES * 8);
	memset(tx->pages, 0, RTL_AX_TX_PAGES * RTL_AX_TX_WP_BYTES);
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
	{
		ret = rtl_ax_tx_pe_start(tx);
		if (ret) goto release;
	}
	return tx;
release:
	rtl_ax_tx_free(tx, NULL);
	return ERR_PTR(ret);
}

int rtl_ax_tx_pause(struct rtl_ax_tx *tx)
{
	const struct rtl_ax_io *io = tx->io;
	struct ieee80211_hw *hw;
	unsigned long flags;
	unsigned int i;
	u32 pe_command = 0;

	spin_lock_irqsave(&tx->lock, flags);
	tx->queues_paused = true;
	hw = tx->hw;
	if (hw) ieee80211_stop_queues(hw);
	spin_unlock_irqrestore(&tx->lock, flags);
	/* Finish dequeued TX callbacks before closing admission to the PE. */
	if (hw) synchronize_net();
	spin_lock_irqsave(&tx->lock, flags);
	tx->running = false;
	spin_unlock_irqrestore(&tx->lock, flags);
	/* Finish accepted requests before transferring the producer to PE-only TX. */
	if (tx->pe_active) {
		u32 consumed;
		if (read_poll_timeout(get_unaligned_le32, consumed, consumed == tx->pe_producer,
		    10, 100000, false, tx->pe.memory + PE_PROBE_MAILBOX + 36))
			return -ETIMEDOUT;
	}
	spin_lock_irqsave(&tx->lock, flags);
	if (tx->pe_active) {
		pe_command = tx->pe_command += 2;
		put_unaligned_le32(pe_command, tx->pe.memory + PE_PROBE_MAILBOX + 12);
		dma_wmb();
	}
	if (!tx->paused) {
		if (!tx->dfs_blocked)
			tx->scheduler_saved = io->read32(io->ctx, 0xc398) & 0x3ffff;
		tx->paused = true;
	}
	/* Actual set_hw_sch_tx_en C/XB writes C398, 18 queue bits.
	 * RFK reason2 additionally waits for band0 TX idle at C6ec[7]. */
	rtl_ax_update32(io, 0xc398, 0x3ffff, 0);
	spin_unlock_irqrestore(&tx->lock, flags);
	if (tx->pe_active) {
		u32 acknowledged;
		if (read_poll_timeout(get_unaligned_le32, acknowledged, acknowledged == pe_command,
		    10, 100000, false, tx->pe.memory + PE_PROBE_MAILBOX + 16))
			return -ETIMEDOUT;
	}
	for (i = 0; i < 2199; i++) {
		if (!(io->read8(io->ctx, 0xc6ec) & BIT(7))) {
			/* The active PPDU still needs DMA until MAC TX becomes idle. */
			rtl_ax_update32(io, 0x1010, 0, RTL_AX_TX_STOP_MASK);
			return 0;
		}
		io->delay_us(io->ctx, 30);
	}
	return -ETIMEDOUT;
}

/* DFS pauses the same18 hardware queues as RFK, without waiting for
 * RPQ inside the RX worker. A later RFK/open must retain the DFS block. */
void rtl_ax_tx_dfs_block(struct rtl_ax_tx *tx, bool blocked)
{
	unsigned long flags;

	spin_lock_irqsave(&tx->lock, flags);
	if (blocked && !tx->dfs_blocked && !tx->paused)
		tx->scheduler_saved = tx->io->read32(tx->io->ctx, 0xc398) & 0x3ffff;
	tx->dfs_blocked = blocked;
	rtl_ax_update32(tx->io, 0xc398, 0x3ffff,
		blocked || tx->paused ? 0 : tx->scheduler_saved | BIT(8));
	spin_unlock_irqrestore(&tx->lock, flags);
}

void rtl_ax_tx_open(struct rtl_ax_tx *tx)
{
	const struct rtl_ax_io *io = tx->io;
	unsigned long flags;

	spin_lock_irqsave(&tx->lock, flags);
	tx->checksum_enabled = !!(io->read32(io->ctx, 0x8830) & BIT(0));
	rtl_ax_update32(io, 0xc398, 0x3ffff, tx->dfs_blocked ? 0 : tx->scheduler_saved | BIT(8));
	rtl_ax_update32(io, 0x1010, RTL_AX_TX_STOP_MASK | BIT(19), 0);
	tx->paused = false;
	tx->running = true;
	tx->queues_paused = false;
	if (tx->pe_active) {
		tx->pe_command += 2;
		put_unaligned_le32(tx->pe_command | 1, tx->pe.memory + PE_PROBE_MAILBOX + 12);
		dma_wmb();
	}
	if (tx->queues_stopped && tx->occupied <=
	    (tx->pe_active && get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 96) ? 48 : RTL_AX_TX_ENTRIES / 2)) {
		tx->queues_stopped = false;
		tx->queue_wakes++;
	}
	if (tx->hw && !tx->queues_stopped)
		ieee80211_wake_queues(tx->hw);
	spin_unlock_irqrestore(&tx->lock, flags);
}

static void rtl_ax_tx_unmap(struct rtl_ax_tx *tx, const struct rtl_ax_tx_slot *slot)
{
	if (slot->mapping) {
		dma_unmap_sg(tx->dma_device, slot->mapping->sg, slot->mapping->count, DMA_TO_DEVICE);
		kfree(slot->mapping);
	} else
		dma_unmap_single(tx->dma_device, slot->dma, slot->bytes, DMA_TO_DEVICE);
}

static int rtl_ax_tx_map(struct rtl_ax_tx *tx, struct sk_buff *skb,
	struct rtl_ax_tx_slot *slot, bool *linearized, bool separate_header)
{
	struct scatterlist *sg;
	unsigned int i, addresses = 0;
	int count, ret;

	if (skb_is_nonlinear(skb)) {
		slot->mapping = kmalloc(sizeof(*slot->mapping), GFP_ATOMIC);
		if (!slot->mapping)
			return -ENOMEM;
		sg_init_table(slot->mapping->sg, RTL_AX_TX_WP_ADDRESS_MAX);
		count = skb_to_sgvec(skb, slot->mapping->sg, 0, skb->len);
		if (count > 0) {
			slot->mapping->count = count;
			slot->mapping->dma_count = dma_map_sg(tx->dma_device, slot->mapping->sg, count, DMA_TO_DEVICE);
			if (!slot->mapping->dma_count) {
				kfree(slot->mapping);
				slot->mapping = NULL;
				return -ENOMEM;
			}
			for_each_sg(slot->mapping->sg, sg, slot->mapping->dma_count, i)
				addresses += DIV_ROUND_UP(sg_dma_len(sg), RTL_AX_TX_WP_FRAGMENT_BYTES);
			if (addresses <= RTL_AX_TX_WP_ADDRESS_MAX - separate_header)
				return addresses;
			rtl_ax_tx_unmap(tx, slot);
		} else
			kfree(slot->mapping);
		slot->mapping = NULL;
		/* The fixed128-byte WP has ten address entries. Compact only an
		 * skb whose fragment layout exceeds that hardware descriptor. */
		ret = skb_linearize(skb);
		if (ret)
			return ret;
		*linearized = true;
	}
	slot->dma = dma_map_single(tx->dma_device, skb->data, skb->len, DMA_TO_DEVICE);
	if (dma_mapping_error(tx->dma_device, slot->dma))
		return -ENOMEM;
	return DIV_ROUND_UP(skb->len, RTL_AX_TX_WP_FRAGMENT_BYTES);
}

void rtl_ax_tx_ap_address(struct rtl_ax_tx *tx, const u8 *address)
{
	if (tx->pe_active) memcpy(tx->pe.memory + PE_PROBE_MAILBOX + 64, address, 6);
}
void rtl_ax_tx_peer_qos(struct rtl_ax_tx *tx, unsigned int macid, bool qos)
{
	tx->peer_qos[macid] = qos;
	tx->peer_offloaded[macid] = false;
	tx->peer_asleep[macid] = false;
	tx->peer_rates[macid] = 0;
	if (tx->pe_active) {
		memset(tx->pe.memory + 0x10000 + macid * 8 * 4, 0, 8 * 4);
		memset(tx->pe.memory + 0xd000 + macid * 32, 0, 32);
		memset(tx->pe.memory + 0x12000 + macid * 8 * 4, 0, 32);
		memset(tx->pe.memory + 0xe400 + macid * 8, 0, 8);
		memset(tx->pe.memory + 0xe800 + macid * 16, 0, 16);
		memset(tx->pe.memory + 0x18000 + macid * 32, 0, 32);
		put_unaligned_le32(0, tx->pe.memory + 0xe200 + macid * 4);
	}
}
void rtl_ax_tx_peer_authorized(struct rtl_ax_tx *tx, unsigned int macid, bool authorized)
{
	WRITE_ONCE(tx->peer_authorized[macid], authorized);
	if (!authorized) {
		/* Revoke PE admission before a fallible FE withdrawal. */
		if (tx->pe_active)
			put_unaligned_le32(0, tx->pe.memory + 0x11a00 + macid * 4);
		dma_wmb();
		rtl_ax_rx_uplink_disable(tx->rx, macid);
	} else if (tx->pe_active && READ_ONCE(tx->ni.route_peers[macid])) {
		put_unaligned_le32(1, tx->pe.memory + 0x11a00 + macid * 4);
		dma_wmb();
		rtl_ax_rx_uplink_enable(tx->rx, macid);
	}
}
void rtl_ax_tx_peer_ba(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid, unsigned int size)
{
	if (tx->pe_active) put_unaligned_le32(size, tx->pe.memory + 0x12000 + (macid * 8 + tid) * 4);
}
void rtl_ax_tx_peer_rate(struct rtl_ax_tx *tx, unsigned int macid, u32 rate, u32 caps)
{
	unsigned long flags;

	spin_lock_irqsave(&tx->lock, flags);
	tx->peer_rates[macid] = rate | BIT(31);
	if (tx->pe_active) {
		put_unaligned_le32(rate, tx->pe.memory + 0xd000 + macid * 32 + 12);
		put_unaligned_le32(caps, tx->pe.memory + 0xe200 + macid * 4);
		dma_wmb();
	}
	spin_unlock_irqrestore(&tx->lock, flags);
}
void rtl_ax_tx_peer_sleep(struct rtl_ax_tx *tx, unsigned int macid, bool asleep)
{
	WRITE_ONCE(tx->peer_asleep[macid], asleep);
	if (tx->pe_active) {
		put_unaligned_le32(asleep, tx->pe.memory + 0xe800 + macid * 16);
		dma_wmb();
	}
	if (asleep) tx->peer_sleep_events++;
	else tx->peer_wake_events++;
	/* mac80211 clears driver-buffered TIDs whenever the peer wakes. */
	WRITE_ONCE(tx->ni.route_tim[macid], ~0U);
	ni_route_sleep(tx, macid);
}
bool rtl_ax_tx_ni_buffered(struct rtl_ax_tx *tx, unsigned int macid)
{
	return tx->pe_active && get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 96) &&
		get_unaligned_le32(tx->pe.memory + 0x11800 + macid * 4);
}
void rtl_ax_tx_ps_release(struct rtl_ax_tx *tx, struct ieee80211_sta *sta,
	unsigned int macid, u16 tids, bool uapsd, bool more_data)
{
	u32 *ps;
	bool queued = false;

	/* ARM in-flight TIDs are announced too, but cannot consume a NI grant. */
	if (rtl_ax_tx_ni_buffered(tx, macid))
		for (unsigned int tid = 0; tid < 8; tid++)
			if ((tids & BIT(tid)) && get_unaligned_le32(tx->pe.memory +
			    0x13000 + (macid * 8 + tid) * 4)) {
				queued = true;
				break;
			}
	if (!queued) {
		ieee80211_sta_eosp(sta);
		return;
	}
	ps = tx->pe.memory + 0xe800 + macid * 16;
	/* Keep TIM tied to pending ownership until RPQ. Clearing it at grant
	 * loses a new arrival while the PE pending mask remains nonzero. */
	put_unaligned_le32(uapsd | more_data << 1 | (u32)tids << 16, ps + 3);
	dma_wmb();
	put_unaligned_le32(get_unaligned_le32(ps + 1) + 1, ps + 1);
	tx->ps_releases++;
}
int rtl_ax_tx_peer_ssn(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid)
{
	int ret = rtl_ax_tx_wait_station(tx, macid, tid);
	if (ret) return ret;
	return tx->pe_active ? get_unaligned_le32(tx->pe.memory + 0x10000 + (macid * 8 + tid) * 4) : 0;
}

int rtl_ax_tx_submit(struct rtl_ax_tx *tx, struct ieee80211_hw *hw,
	struct sk_buff *skb, unsigned int rate, unsigned int gi, unsigned int bandwidth, unsigned int macid,
	unsigned int aggregate, unsigned int density)
{
	struct ieee80211_tx_info *info = IEEE80211_SKB_CB(skb);
	struct ieee80211_hdr *header = (void *)skb->data;
	static const u8 tid_indicators[] = { 0, 0, 1, 1, 0, 1, 0, 1 };
	unsigned int id, next, addresses, length, ring, tid = 0, qsel;
	unsigned long flags;
	struct rtl_ax_tx_slot slot = {.skb = skb, .bytes = skb->len};
	bool linearized = false;
	bool encap = info->flags & IEEE80211_TX_CTL_HW_80211_ENCAP;
	bool pe_sequence = tx->pe_active && !encap && macid &&
		ieee80211_is_data_qos(header->frame_control) && ieee80211_is_data_present(header->frame_control) &&
		!ieee80211_has_morefrags(header->frame_control) && !(le16_to_cpu(header->seq_ctrl) & 15);
	unsigned int header_bytes = encap ? tx->peer_qos[macid] ? 26 : 24 : ieee80211_hdrlen(header->frame_control);
	unsigned int packet_bytes = encap ? skb->len + header_bytes + 8 - 14 : skb->len;
	u8 *page;
	int ret;
	u64 begin = READ_ONCE(rtl_ax_datapath_profile) ? ktime_get_ns() : 0;
	if (packet_bytes > 0x3fff) return -EMSGSIZE;

	if (!encap && ieee80211_is_mgmt(header->frame_control)) {
		ring = 4;
		qsel = 0x12;
	} else if (info->flags & IEEE80211_TX_CTL_SEND_AFTER_DTIM) {
		ring = 5;
		qsel = 0x11;
	} else {
		if (encap) tid = skb->priority & 7;
		else if (ieee80211_is_data_qos(header->frame_control))
			tid = *ieee80211_get_qos_ctl(header) & 7;
		ring = rtl_ax_tx_dma_channel(tid, 0);
		qsel = ring;
	}
	ret = rtl_ax_tx_map(tx, skb, &slot, &linearized, encap || pe_sequence);
	if (ret < 0)
		return ret;
	addresses = ret;
	header = (void *)skb->data;
	spin_lock_irqsave(&tx->lock, flags);
	if (!tx->running || tx->dfs_blocked) {
		tx->stopped++;
		ret = -ENETDOWN;
		goto unmap;
	}
	id = find_first_zero_bit(tx->pending, RTL_AX_TX_ENTRIES);
	next = (tx->producer[ring] + 1) & (RTL_AX_TX_ENTRIES - 1);
	/* Every RPQ release follows consumption of its TXBD. The shared WP
	 * credit limit stops all queues at224, before any256-entry ring fills. */
	if (id == RTL_AX_TX_ENTRIES) {
		tx->full++;
		ret = -ENOSPC;
		goto unmap;
	}
	page = tx->pages + id * RTL_AX_TX_WP_BYTES;
	if (encap || ieee80211_is_data(header->frame_control))
		rate |= tx->peer_rates[macid] & BIT(22);
	if (tx->pe_active) {
		u32 *request = tx->pe.memory + 0x4000 + tx->pe_producer * 128;
		unsigned int segments = slot.mapping ? slot.mapping->dma_count : 1;
		u32 request_flags = (encap ? tid : le16_to_cpu(header->seq_ctrl) >> 4) | tid_indicators[tid] << 12 |
			!!(info->flags & IEEE80211_TX_CTL_NO_ACK) << 13 |
			is_multicast_ether_addr(encap ? skb->data : header->addr1) << 14 | tx->checksum_enabled << 17 | macid << 24 | encap << 18;
		u32 key = 0; u64 pn = 0;
		if (pe_sequence)
			request_flags = (request_flags & ~0xfffU) | tid | BIT(22);
		if (info->control.hw_key) {
			key = info->control.hw_key->hw_key_idx << 16 | info->control.hw_key->keyidx << 24;
			if ((info->control.hw_key->flags & IEEE80211_KEY_FLAG_PAIRWISE) &&
			    (encap || ieee80211_is_data(header->frame_control))) {
				request_flags |= BIT(16);
				atomic64_inc(&info->control.hw_key->tx_pn); tx->hardware_pn++;
			} else {
				request_flags |= BIT(15);
				pn = atomic64_inc_return(&info->control.hw_key->tx_pn);
			}
			tx->encrypted++;
		}
		request[0] = id;
		request[1] = ring | tx->producer[ring] << 8 | next << 16;
		request[2] = packet_bytes | header_bytes << 16 | segments << 24;
		tx->pe_ethernet += encap;
		request[3] = rate | gi << 12 | bandwidth << 16;
		if (encap) request[3] |= tx->peer_rates[macid] & (BIT(21) | BIT(22));
		if (encap && (get_unaligned_le32(tx->pe.memory + 0xe200 + macid * 4) & BIT(17)))
			request[3] |= BIT(20);
		request[4] = request_flags; request[5] = aggregate | density << 8 | key;
		request[6] = pn; request[7] = pn >> 32;
		if (slot.mapping) {
			struct scatterlist *sg; unsigned int i;
			for_each_sg(slot.mapping->sg, sg, segments, i) {
				request[8 + i * 2] = lower_32_bits(sg_dma_address(sg));
				request[9 + i * 2] = sg_dma_len(sg);
			}
			tx->scatter_packets++; tx->scatter_segments += segments;
		} else { request[8] = lower_32_bits(slot.dma); request[9] = skb->len; }
		if (encap) {
			tx->last_eth_dma = request[8];
			memcpy(tx->last_eth, skb->data, sizeof(tx->last_eth));
			if (!tid) {
				memcpy(tx->pe.memory + 0xd000 + macid * 32, request, 32);
				if (tx->peer_rates[macid] & BIT(31))
					put_unaligned_le32(tx->peer_rates[macid] & ~BIT(31), tx->pe.memory + 0xd000 + macid * 32 + 12);
			}
			memcpy(tx->pe.memory + 0xe400 + macid * 8, skb->data, 6);
		}
	} else {
	memset(page, 0, RTL_AX_TX_WP_BYTES);
	if (slot.mapping) {
		struct scatterlist *sg;
		unsigned int i, written = 0;

		for_each_sg(slot.mapping->sg, sg, slot.mapping->dma_count, i)
			written += rtl_ax_tx_wp_addresses(page + 56 + RTL_AX_TX_WP_SEQUENCE_BYTES +
				written * RTL_AX_TX_WP_ADDRESS_BYTES, lower_32_bits(sg_dma_address(sg)),
				sg_dma_len(sg), i + 1 == slot.mapping->dma_count);
		tx->scatter_packets++;
		tx->scatter_segments += slot.mapping->dma_count;
	} else
		rtl_ax_tx_wp_addresses(page + 56 + RTL_AX_TX_WP_SEQUENCE_BYTES, lower_32_bits(slot.dma), skb->len, true);
	length = 56 + RTL_AX_TX_WP_SEQUENCE_BYTES + addresses * RTL_AX_TX_WP_ADDRESS_BYTES;
	rtl_ax_tx_wd(page, skb->len, addresses, rate, gi, bandwidth,
		le16_to_cpu(header->seq_ctrl) >> 4, ieee80211_hdrlen(header->frame_control),
		info->flags & IEEE80211_TX_CTL_NO_ACK, is_multicast_ether_addr(header->addr1),
		rtl_ax_tx_channels[ring], qsel, tid_indicators[tid], macid, aggregate, density);
	if (info->control.hw_key) {
		rtl_ax_store32(page, rtl_ax_ap_read32(page) | BIT(24));
		if ((info->control.hw_key->flags & IEEE80211_KEY_FLAG_PAIRWISE) &&
		    (encap || ieee80211_is_data(header->frame_control))) {
			rtl_ax_store32(page, rtl_ax_ap_read32(page) | BIT(6));
			rtl_ax_store32(page + 4, rtl_ax_ap_read32(page + 4) | 6 |
				info->control.hw_key->keyidx << 4);
			rtl_ax_store32(page + 40, rtl_ax_ap_read32(page + 40) | BIT(8) |
				info->control.hw_key->hw_key_idx);
			atomic64_inc(&info->control.hw_key->tx_pn);
			tx->hardware_pn++;
		} else
			rtl_ax_security_tx(page, info->control.hw_key->hw_key_idx,
				info->control.hw_key->keyidx, atomic64_inc_return(&info->control.hw_key->tx_pn));
		tx->encrypted++;
	}
	if (tx->checksum_enabled)
		rtl_ax_tx_checksum(page);
	rtl_ax_tx_wp_sequence(page + 56, id | (tid_indicators[tid] ? BIT(14) : 0));
	}
	slot.ring = ring;
	slot.pe_sequence = pe_sequence;
	slot.macid = macid;
	slot.tid = encap ? tid : ieee80211_is_data_qos(header->frame_control) ? tid : IEEE80211_NUM_TIDS;
	slot.encrypted = !!info->control.hw_key;
	slot.key_index = info->control.hw_key ? info->control.hw_key->hw_key_idx : 0;
	tx->slots[id] = slot;
	__set_bit(id, tx->pending);
	tx->occupied++;
	if (!tx->pe_active)
		rtl_ax_txbd(tx->ring + (ring * RTL_AX_TX_ENTRIES + tx->producer[ring]) * 8,
		lower_32_bits(tx->pages_dma + id * RTL_AX_TX_WP_BYTES), length);
	dma_wmb();
	tx->producer[ring] = next;
	tx->submitted++;
	tx->linearized_packets += linearized;
	tx->amsdu_packets += !encap && ieee80211_is_data_qos(header->frame_control) &&
		(*ieee80211_get_qos_ctl(header) & IEEE80211_QOS_CTL_A_MSDU_PRESENT);
	tx->fast_xmit += !!(info->control.flags & IEEE80211_TX_CTRL_FAST_XMIT);
	if (begin)
		tx->prepare_ns += ktime_get_ns() - begin;
	tx->ampdu_submitted += !!aggregate;
	tx->submitted_ring[ring]++;
	tx->last_sequence = id;
	if (!tx->pe_active) memcpy(tx->last_words, page, sizeof(tx->last_words));
	if (tx->pe_active) {
		tx->pe_producer = (tx->pe_producer + 1) & 255;
		put_unaligned_le32(tx->pe_producer, tx->pe.memory + PE_PROBE_MAILBOX + 32);
		dma_wmb();
	} else
		tx->io->write16(tx->io->ctx, rtl_ax_tx_indices[ring], next);
	/* WP IDs are shared by all six DMA rings. Pause mac80211 dequeue
	 * before exhausting them; RPQ release owns the matching wake. */
	if (tx->occupied >= (tx->pe_active && get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 96) ? 96 : RTL_AX_TX_ENTRIES - 32) && !tx->queues_stopped) {
		tx->queues_stopped = true;
		tx->queue_stops++;
		ieee80211_stop_queues(hw);
	}
	spin_unlock_irqrestore(&tx->lock, flags);
	if (READ_ONCE(tx->peer_asleep[macid])) ni_route_sleep(tx, macid);
	if (encap && tx->pe_active && tx->peer_ready && !READ_ONCE(tx->peer_offloaded[macid]))
		tx->peer_ready(tx->peer_ready_context);
	return 0;
unmap:
	spin_unlock_irqrestore(&tx->lock, flags);
	rtl_ax_tx_unmap(tx, &slot);
	return ret;
}

static void rtl_ax_tx_pe_complete(struct rtl_ax_tx *tx, unsigned int id, unsigned int status)
{
	unsigned int member = id - 256;

	void *entry = tx->pe.memory + 0xc400 + member * 8;
	u32 details = get_unaligned_le32(entry + 4);
	put_unaligned_le32(details | BIT(31) | status, entry + 4);
	put_unaligned_le32(member, tx->pe.memory + 0xc800 + tx->ni_rpq_producer * 4);
	tx->ni_rpq_producer = (tx->ni_rpq_producer + 1) & 127;
	dma_wmb();
	put_unaligned_le32(tx->ni_rpq_producer, tx->pe.memory + PE_PROBE_MAILBOX + 180);
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
		__le16 completed_sequence = 0;
		if (packet->pe_completed && id >= 256 && id < RTL_AX_TX_PAGES) {
			struct ieee80211_sta *sta;
			unsigned int macid = report & 127;
			local_bh_disable(); rcu_read_lock();
			sta = ieee80211_find_sta_by_ifaddr(hw, tx->pe.memory + 0xe400 + macid * 8, NULL);
			if (sta) ieee80211_sta_eosp(sta);
			rcu_read_unlock(); local_bh_enable();
			tx->ps_eosp++;
			continue;
		}
		if (tx->pe_active && id >= 256 && id < RTL_AX_TX_PAGES) {
			void *owner = tx->pe.memory + 0xc400 + (id - 256) * 8;
			u32 meta = get_unaligned_le32(owner + 4);
			u32 rate = get_unaligned_le32(tx->pe.memory + 0xe000 + (id - 256) * 4);
			unsigned int macid = (meta >> 8) & 127;
			struct ieee80211_tx_info feedback = {.band = NL80211_BAND_5GHZ};
			struct ieee80211_tx_status feedback_status = {.info = &feedback};
			feedback.status.rates[0].idx = (rate & 511) - 0x80;
			/* RPQ supplies ownership/status, not the firmware-selected PHY rate. */
			if (rate & BIT(20)) feedback.status.rates[0].idx = -1;
			feedback.status.rates[0].flags = IEEE80211_TX_RC_MCS |
				(rate & BIT(12) ? IEEE80211_TX_RC_SHORT_GI : 0);
			feedback.status.rates[0].count = 1;
			feedback.status.rates[1].idx = -1;
			feedback.flags = (!status ? IEEE80211_TX_STAT_ACK : 0) |
				(rate & BIT(19) ? IEEE80211_TX_CTL_RATE_CTRL_PROBE : 0);
			local_bh_disable(); rcu_read_lock();
			feedback_status.sta = ieee80211_find_sta_by_ifaddr(hw, tx->pe.memory + 0xe400 + macid * 8, NULL);
			if (feedback_status.sta) ieee80211_tx_status_ext(hw, &feedback_status);
			rcu_read_unlock(); local_bh_enable();
			if (!tx->ni_reports) tx->ni_first_report_ns = ktime_get_ns();
			rtl_ax_tx_pe_complete(tx, id, status);
			tx->ni_reports++; tx->ni_failed += !!status;
			wake_up_all(&tx->key_flush_wait);
			if (meta & BIT(16)) {
				struct ieee80211_sta *sta;
				unsigned int macid = (meta >> 8) & 127;
				local_bh_disable(); rcu_read_lock();
				sta = ieee80211_find_sta_by_ifaddr(hw, tx->pe.memory + 0xe400 + macid * 8, NULL);
				if (sta) ieee80211_sta_eosp(sta);
				rcu_read_unlock(); local_bh_enable();
				tx->ps_eosp++;
			}
			continue;
		}

		spin_lock_irqsave(&tx->lock, flags);
		tx->last_report = report;
		if (id >= RTL_AX_TX_ENTRIES || !test_bit(id, tx->pending) || tx->slots[id].ring != ring) {
			tx->unmatched++;
			spin_unlock_irqrestore(&tx->lock, flags);
			continue;
		}
		slot = tx->slots[id];
		if (slot.pe_sequence)
			completed_sequence = get_unaligned((__le16 *)(tx->headers + id * 128 + 22));
		tx->slots[id].skb = NULL;
		__clear_bit(id, tx->pending);
		tx->occupied--;
		tx->completed++;
		tx->completed_ring[ring]++;
		tx->status_count[status]++;
		tx->polluted += !!(report & BIT(31));
		if (tx->queues_stopped && tx->running && !tx->queues_paused && tx->hw && tx->occupied <=
		    (tx->pe_active && get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 96) ? 48 : RTL_AX_TX_ENTRIES / 2)) {
			tx->queues_stopped = false;
			tx->queue_wakes++;
			ieee80211_wake_queues(hw);
		}
		spin_unlock_irqrestore(&tx->lock, flags);
		if (READ_ONCE(tx->peer_asleep[slot.macid])) ni_route_sleep(tx, slot.macid);
		wake_up_all(&tx->key_flush_wait);
		rtl_ax_tx_unmap(tx, &slot);
		if (slot.pe_sequence)
			((struct ieee80211_hdr *)slot.skb->data)->seq_ctrl = completed_sequence;
		info = IEEE80211_SKB_CB(slot.skb);
		ieee80211_tx_info_clear_status(info);
		if ((info->flags & IEEE80211_TX_CTL_HW_80211_ENCAP) &&
		    (get_unaligned_le32(tx->pe.memory + 0xe200 + slot.macid * 4) & BIT(17)))
			info->status.rates[0].idx = -1;
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
		if (info->flags & IEEE80211_TX_CTL_HW_80211_ENCAP) {
			struct ieee80211_tx_status status = {.skb = slot.skb, .info = info};
			local_bh_disable(); rcu_read_lock();
			status.sta = ieee80211_find_sta_by_ifaddr(hw, slot.skb->data, NULL);
			ieee80211_tx_status_ext(hw, &status);
			rcu_read_unlock(); local_bh_enable();
		} else ieee80211_tx_status_ni(hw, slot.skb);
	}
	return 0;
}

static bool rtl_ax_tx_pe_pending(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid,
	unsigned int key, bool by_key)
{
	unsigned int i;

	if (!tx->pe_active) return false;
	if (!by_key)
		for (i = 0; i < 8; i++)
			if ((tid == IEEE80211_NUM_TIDS || tid == i) &&
			    get_unaligned_le32(tx->pe.memory + 0x13000 + (macid * 8 + i) * 4)) return true;
	/* NI_WAIT retains the submitted TID; RPQ done ends the MAC/key wait. */
	for (i = 0; i < 128; i++) {
		void *owner = tx->pe.memory + 0xc400 + i * 8;
		u32 meta = get_unaligned_le32(owner + 4);
		if (!get_unaligned_le32(owner) || (meta & BIT(31))) continue;
		if (by_key ? ((meta >> 17) & 127) == key :
		    ((meta >> 8) & 127) == macid && (tid == IEEE80211_NUM_TIDS ||
		    (get_unaligned_le32(tx->pe.memory + 0x11000 + i * 16 + 12) & 7) == tid)) return true;
	}
	return false;
}

static bool rtl_ax_tx_key_pending(struct rtl_ax_tx *tx, unsigned int index)
{
	unsigned long flags;
	unsigned int id;
	bool pending = false;

	spin_lock_irqsave(&tx->lock, flags);
	for_each_set_bit(id, tx->pending, RTL_AX_TX_ENTRIES)
		if (tx->slots[id].encrypted && tx->slots[id].key_index == index) {
			pending = true;
			break;
		}
	spin_unlock_irqrestore(&tx->lock, flags);
	return pending || rtl_ax_tx_pe_pending(tx, 0, IEEE80211_NUM_TIDS, index, true);
}

static void rtl_ax_tx_waiters(struct rtl_ax_tx *tx, int delta)
{
	unsigned long flags;
	void *count;

	if (!tx->pe_active) return;
	count = tx->pe.memory + PE_PROBE_MAILBOX + 312;
	spin_lock_irqsave(&tx->lock, flags);
	put_unaligned_le32(get_unaligned_le32(count) + delta, count);
	dma_wmb();
	spin_unlock_irqrestore(&tx->lock, flags);
}

int rtl_ax_tx_flush_key(struct rtl_ax_tx *tx, unsigned int index)
{
	int ret;

	rtl_ax_tx_waiters(tx, 1);
	/* RPQ completion relinquishes the key before its CAM slot is reused. */
	ret = wait_event_timeout(tx->key_flush_wait, !rtl_ax_tx_key_pending(tx, index),
		msecs_to_jiffies(1000)) ? 0 : -ETIMEDOUT;
	rtl_ax_tx_waiters(tx, -1);
	return ret;
}

bool rtl_ax_tx_station_pending(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid)
{
	unsigned long flags;
	unsigned int id;
	bool pending = false;

	spin_lock_irqsave(&tx->lock, flags);
	for_each_set_bit(id, tx->pending, RTL_AX_TX_ENTRIES)
		if (tx->slots[id].macid == macid &&
		    (tid == IEEE80211_NUM_TIDS || tx->slots[id].tid == tid)) {
			pending = true;
			break;
		}
	spin_unlock_irqrestore(&tx->lock, flags);
	return pending || rtl_ax_tx_pe_pending(tx, macid, tid, 0, false);
}

int rtl_ax_tx_wait_station(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid)
{
	int ret;

	rtl_ax_tx_waiters(tx, 1);
	ret = wait_event_timeout(tx->key_flush_wait, !rtl_ax_tx_station_pending(tx, macid, tid),
		msecs_to_jiffies(1000)) ? 0 : -ETIMEDOUT;
	rtl_ax_tx_waiters(tx, -1);
	return ret;
}

int rtl_ax_tx_free(struct rtl_ax_tx *tx, struct ieee80211_hw *hw)
{
	unsigned int i;
	int ret;

	if (!tx)
		return 0;
	ret = ni_test_remove(tx);
	/* Unsubscribe ARM callbacks even when DMA allocations must stay owned. */
	pe_event_release(tx);
	cancel_work_sync(&tx->ni.ps_work);
	if (ret) return ret;
	if (tx->pe.memory) {
		ret = pe_probe_stop(&tx->pe);
		if (ret) return ret;
	}
	/* Caller has closed RX work and removed device DMA ownership. */
	for (i = 0; i < RTL_AX_TX_ENTRIES; i++)
		if (tx->slots[i].skb) {
			rtl_ax_tx_unmap(tx, &tx->slots[i]);
			ieee80211_free_txskb(hw, tx->slots[i].skb);
		}
	if (tx->headers) dma_free_coherent(tx->dma_device, RTL_AX_TX_PAGES * 128, tx->headers, tx->headers_dma);
	if (tx->pages)
		dma_free_coherent(tx->dma_device, RTL_AX_TX_PAGES * RTL_AX_TX_WP_BYTES, tx->pages, tx->pages_dma);
	if (tx->ring)
		dma_free_coherent(tx->dma_device, RTL_AX_TX_RINGS * RTL_AX_TX_ENTRIES * 8, tx->ring, tx->ring_dma);
	if (tx->pe.memory)
		pe_probe_free(&tx->pe);
	kfree(tx);
	return 0;
}

int rtl_ax_tx_status(struct rtl_ax_tx *tx, char *out, size_t capacity)
{
	unsigned long flags;
	unsigned int i, report_config;
	int length;

	spin_lock_irqsave(&tx->lock, flags);
	if (tx->pe_active) {
		/* PE owns WP IDs 256..383; truncating to eight bits shows an ARM WD. */
		unsigned int id = get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 52);
		memcpy(tx->last_words, tx->pages + id * RTL_AX_TX_WP_BYTES, sizeof(tx->last_words));
	}
	length = scnprintf(out, capacity, "native_TX_running=%u submitted=%llu completed=%llu pending=%u "
		"last_sequence=%u last_RPQ=%08x polluted=%llu unmatched=%llu full=%llu stopped=%llu "
		"scheduler=%08x DMA_stop=%08x WD_checksum=%08x\n",
		tx->running, tx->submitted, tx->completed, bitmap_weight(tx->pending, RTL_AX_TX_ENTRIES),
		tx->last_sequence, tx->last_report, tx->polluted, tx->unmatched, tx->full, tx->stopped,
		tx->io->read32(tx->io->ctx, 0xc398), tx->io->read32(tx->io->ctx, 0x1010), tx->io->read32(tx->io->ctx, 0x8830));
	report_config = tx->io->read32(tx->io->ctx, 0x9414);
	length += scnprintf(out + length, capacity - length,
		"RPQ_release_config=%08x aggregate_reports=%u timeout_code=%u\n",
		report_config, report_config & 255, (report_config >> 16) & 255);
	for (i = 0; i < RTL_AX_TX_RINGS; i++) {
		u32 index = tx->io->read32(tx->io->ctx, rtl_ax_tx_indices[i]);

		length += scnprintf(out + length, capacity - length,
			"DMA_channel=%u submitted=%llu completed=%llu producer=%u hardware=%u ARM_producer=%u\n",
			rtl_ax_tx_channels[i], tx->submitted_ring[i], tx->completed_ring[i],
			index & 0x3ff, (index >> 16) & 0x3ff, tx->producer[i]);
	}
	length += scnprintf(out + length, capacity - length,
		"queues_stopped=%u queue_stops=%llu queue_wakes=%llu\n",
		tx->queues_stopped, tx->queue_stops, tx->queue_wakes);
	length += scnprintf(out + length, capacity - length, "AMPDU_submitted_MPDU=%llu AMPDU_completed_MPDU=%llu\n",
		tx->ampdu_submitted, tx->ampdu_completed);
	length += scnprintf(out + length, capacity - length, "hardware_encrypted_MPDU=%llu\n", tx->encrypted);
	length += scnprintf(out + length, capacity - length, "hardware_PN_MPDU=%llu\n", tx->hardware_pn);
	length += scnprintf(out + length, capacity - length, "host_TX_prepare_ns=%llu\n", tx->prepare_ns);
	length += scnprintf(out + length, capacity - length, "mac80211_fast_xmit_MPDU=%llu\n", tx->fast_xmit);
	length += scnprintf(out + length, capacity - length,
		"scatter_MPDU=%llu scatter_DMA_segments=%llu AMSDU_MPDU=%llu linearized_MPDU=%llu\n",
		tx->scatter_packets, tx->scatter_segments, tx->amsdu_packets, tx->linearized_packets);
	if (tx->pe_active)
		length += scnprintf(out + length, capacity - length,
			"PE_TX_descriptors=%u PE_Cause=%08x PE_EPC=%08x PE_ETH_frames=%llu\n",
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 4),
			get_unaligned_le32(pe_engine->memory + PE_PROBE_MAILBOX + 20),
			get_unaligned_le32(pe_engine->memory + PE_PROBE_MAILBOX + 24), tx->pe_ethernet);
	for (i = 0; i < 8; i++)
		length += scnprintf(out + length, capacity - length, "TX_status=%u count=%llu\n", i, tx->status_count[i]);
	if (tx->pe_active && tx->pe_ethernet) {
		unsigned int id = get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 72) & 4095;
		length += scnprintf(out + length, capacity - length,
			"PE_ETH_flags=%08x packet=%08x peer1_QoS=%u",
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 76),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 80),
			tx->peer_qos[1]);
		if (id < RTL_AX_TX_ENTRIES)
			length += scnprintf(out + length, capacity - length,
				" header=%34phN\n", tx->headers + id * 128);
		else
			length += scnprintf(out + length, capacity - length, " header=in-place-NI\n");
		length += scnprintf(out + length, capacity - length,
			"PE_ETH_DMA=%08x host_DMA=%08x host_Ethernet=%14phN PE_RAM=%pad header_DMA=%pad\n",
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 84),
			tx->last_eth_dma, tx->last_eth, &tx->pe.dma, &tx->headers_dma);
	}
	if (tx->pe_active)
		length += scnprintf(out + length, capacity - length,
			"PE_PRID=%08x Status=%08x Config0=%08x Config1=%08x CCTL_boot=%08x entry=%08x CCTL_run=%08x\n",
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 192),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 196),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 200),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 204),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 208),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 212),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 216));
	if (tx->pe_active)
		length += scnprintf(out + length, capacity - length,
			"PE_slot=%u NI_CPU=%u EQ=%u VP=%u\n",
			tx->pe.slot, tx->ni.cpu, tx->ni.eq, tx->ni.vp_index);
	if (tx->pe_active)
		length += scnprintf(out + length, capacity - length,
			"PE_NI_MPDU=%u AMSDU=%u PEDIV=%08x stack=%08x event_IRQ=%llu event_generation=%u event_ACK=%u\n",
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 224),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 228),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 240),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 244), pe_event_interrupts,
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 268),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 264));
	if (tx->pe_active)
		length += scnprintf(out + length, capacity - length,
			"PE_NI_sent=%u recycled=%u pending=%u RPQ_reports=%llu failed=%llu PS_releases=%llu PS_EOSP=%llu invalid_EPP=%u retired_EPP=%u\n",
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 112),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 116),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 120), ni_reports(tx), ni_failed(tx),
			tx->ps_releases, tx->ps_eosp, get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 160),
			get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 252));
	if (tx->pe_active)
		length += scnprintf(out + length, capacity - length,
			"PE_NI_reject_EPP=%08x/%08x length=%u header=%u Ethernet=%08x/%08x\n",
			get_unaligned_le32(tx->pe.memory + 0x2200), get_unaligned_le32(tx->pe.memory + 0x2204),
			get_unaligned_le32(tx->pe.memory + 0x2208), get_unaligned_le32(tx->pe.memory + 0x220c),
			get_unaligned_le32(tx->pe.memory + 0x2210), get_unaligned_le32(tx->pe.memory + 0x2214));
	if (tx->pe_active)
		for (i = 1; i < 128; i++) {
			void *peer = tx->pe.memory + 0xd000 + i * 32;
			if (!(get_unaligned_le32(peer + 16) & BIT(18))) continue;
			length += scnprintf(out + length, capacity - length,
				"PE_peer=%u MAC=%pM rate=%08x flags=%08x aggregation_key=%08x asleep=%u sleep_events=%llu wake_events=%llu\n", i,
				tx->pe.memory + 0xe400 + i * 8, get_unaligned_le32(peer + 12),
				get_unaligned_le32(peer + 16), get_unaligned_le32(peer + 20),
				tx->peer_asleep[i], tx->peer_sleep_events, tx->peer_wake_events);
			length += scnprintf(out + length, capacity - length,
				"PE_downlink_peer=%u frames=%u TIDs=%04x queued=%u\n", i,
				get_unaligned_le32(tx->pe.memory + 0x17000 + i * 4),
				get_unaligned_le32(tx->pe.memory + 0x17200 + i * 4),
				get_unaligned_le32(tx->pe.memory + 0x11800 + i * 4));
		}
	for (i = 0; i < 14; i++)
		length += scnprintf(out + length, capacity - length, "WD_word=%u value=%08x\n", i, tx->last_words[i]);
	spin_unlock_irqrestore(&tx->lock, flags);
	return length;
}

#include "ni-test.h"
