// SPDX-License-Identifier: GPL-2.0-only
/* Per-radio NI VP/EPP/EQ state, consumed by the shared PE1 executor. */
#include <linux/etherdevice.h>
#include <linux/rtl9617c-qm-layout.h>

#define RING_BYTES RTL9617C_WIFI_EPP_BYTES
#define BUFFER_COUNT RTL9617C_WIFI_BUFFERS
#define EPP_CONSUMERS 0x11c00
#define EPP_RETIRE_TARGETS 0x19400
#define PACKETS 8192
#define TEST_FRAME_BYTES 1500
#define TEST_MEMORY_BYTES (0x4000 + RTL_AX_NI_TX_ENTRIES * 2048)
static DEFINE_MUTEX(ni_test_lock);
static void ni_route_ps(struct work_struct *work);
#include "pe-event.h"

static void ni_state_init(struct rtl_ax_tx *tx)
{
 struct rtl_ax_ni_state *state = &tx->ni;
 unsigned int slot = tx->pdev->device == 0xc852 ? 0 : 1;
 state->tx = tx; state->cpu = 3 - slot; state->eq = 6 + slot;
 state->profile = 7 - slot; state->vp_index = 9 + slot;
 state->registers[0] = 0x61e8 + state->eq * 20;
 for (unsigned int i = 1; i < 5; i++) state->registers[i] = state->registers[0] + i * 4;
 state->registers[5] = 0x6128 + state->profile * 4;
 state->registers[6] = 0x6148 + state->cpu * 4;
 state->registers[7] = 0x61c8 + state->cpu * 4;
 state->registers[8] = 0x6558 + state->cpu * 4;
 state->registers[9] = 0x657c + state->profile * 4;
 state->registers[10] = 0x65a4 + state->cpu * 32;
 state->registers[11] = 0x7100 + state->cpu * 32;
 state->registers[12] = 0x7200 + state->cpu * 32;
 state->registers[13] = 0x6108;
 INIT_WORK(&state->ps_work, ni_route_ps);
}

static void ni_test_buffered(struct rtl_ax_tx *tx, unsigned int macid, u16 tids)
{
	struct ieee80211_sta *sta;
	unsigned int tid;
	local_bh_disable(); rcu_read_lock();
	sta = ieee80211_find_sta_by_ifaddr(tx->hw, tx->pe.memory + 0xe400 + macid * 8, NULL);
	for (tid = 0; sta && tid < 8; tid++)
		ieee80211_sta_set_buffered(sta, tid, tids & BIT(tid));
	rcu_read_unlock(); local_bh_enable();
}

static void ni_ps_done(struct rtl_ax_tx *tx, unsigned int macid)
{
	struct ieee80211_sta *sta;
	static const u32 tables[] = { RTL_AX_NI_PS_CANCEL, RTL_AX_NI_PS_DONE };
	unsigned int i;

	local_bh_disable(); rcu_read_lock();
	sta = ieee80211_find_sta_by_ifaddr(tx->hw, tx->pe.memory + 0xe400 + macid * 8, NULL);
	for (i = 0; sta && i < 2; i++) {
		u8 *record = tx->pe.memory + tables[i] + macid * 8;
		u32 producer = get_unaligned_le32(record + 4);

		if (producer == tx->ni.ps_seen[i][macid]) continue;
		dma_rmb();
		u32 generation = get_unaligned_le32(record);
		tx->ni.ps_seen[i][macid] = producer;
		ni_ps_complete(tx, sta, macid, generation);
	}
	rcu_read_unlock(); local_bh_enable();
}

static void ni_ps_retire(struct rtl_ax_tx *tx)
{
	unsigned int macid;

	/* PE is stopped, all WPs and RX callbacks have drained. */
	local_bh_disable(); rcu_read_lock();
	for (macid = 1; macid < 128; macid++) {
		if (!tx->ni.ps_issued[macid]) continue;
		struct ieee80211_sta *sta = ieee80211_find_sta_by_ifaddr(tx->hw,
			tx->pe.memory + 0xe400 + macid * 8, NULL);
		if (sta) ni_ps_complete(tx, sta, macid,
			get_unaligned_le32(tx->pe.memory + 0xe800 + macid * 16 + 4));
		tx->ni.ps_issued[macid] = false;
	}
	rcu_read_unlock(); local_bh_enable();
}

static void ni_tx_activity(struct rtl_ax_tx *tx, unsigned int macid)
{
	struct ieee80211_sta *sta;
	unsigned int tid, changed = 0;

	if (!READ_ONCE(tx->peer_authorized[macid])) return;
	dma_rmb();
	for (tid = 0; tid < 8; tid++) {
		u32 packets = get_unaligned_le32(tx->pe.memory + RTL_AX_NI_TX_ACTIVITY + (macid * 8 + tid) * 4);
		if (packets == tx->ni.tx_activity_seen[macid][tid]) continue;
		tx->ni.tx_activity_seen[macid][tid] = packets;
		changed |= BIT(tid);
	}
	if (!changed) return;
	/* NI traffic bypasses mac80211's TX preparation and its last_tx update. */
	local_bh_disable(); rcu_read_lock();
	sta = ieee80211_find_sta_by_ifaddr(tx->hw, tx->pe.memory + 0xe400 + macid * 8, NULL);
	for (tid = 0; sta && tid < 8; tid++)
		if (changed & BIT(tid)) {
			ieee80211_refresh_tx_agg_session_timer(sta, tid);
			tx->ni.ba_refreshes++;
		}
	rcu_read_unlock(); local_bh_enable();
}

static void ni_route_ps(struct work_struct *work)
{
	struct rtl_ax_tx *tx = container_of(work, struct rtl_ax_ni_state, ps_work)->tx;
	u16 pending[128] = {};
	unsigned int macid, tid, id;
	unsigned long flags;

	/* Original stop_sta_xmit includes num_pending_txreq in its TIM decision. */
	spin_lock_irqsave(&tx->lock, flags);
	for_each_set_bit(id, tx->pending, RTL_AX_TX_ENTRIES)
		if (tx->slots[id].tid < 8)
			pending[tx->slots[id].macid] |= BIT(tx->slots[id].tid);
	spin_unlock_irqrestore(&tx->lock, flags);
	for (macid = 1; macid < 128; macid++) {
		u32 mask = 0;
		if (tx->pe_active) {
			ni_ps_done(tx, macid);
			ni_tx_activity(tx, macid);
		}
		if (!READ_ONCE(tx->peer_authorized[macid]) && !READ_ONCE(tx->ni.route_tim[macid])) continue;
		if (READ_ONCE(tx->peer_asleep[macid])) {
			mask = pending[macid];
			for (tid = 0; tid < 8; tid++)
				if (tx->pe_active && get_unaligned_le32(tx->pe.memory + 0x18000 + (macid * 8 + tid) * 4)) mask |= BIT(tid);
		}
		if (mask != READ_ONCE(tx->ni.route_tim[macid])) {
			ni_test_buffered(tx, macid, mask);
			WRITE_ONCE(tx->ni.route_tim[macid], mask);
		}
	}
}

static void ni_route_sleep(struct rtl_ax_tx *tx, unsigned int macid)
{
	if (READ_ONCE(tx->peer_authorized[macid])) schedule_work(&tx->ni.ps_work);
}

static int ni_access(struct rtl_ax_tx *tx, unsigned int offset, u32 command)
{
	u32 status;
	writel(command, tx->ni.regs + offset);
	return readl_poll_timeout(tx->ni.regs + offset, status, !(status & BIT(31)), 1, 100000);
}

static int attr_write(struct rtl_ax_tx *tx, unsigned int index, const u32 *value)
{
	writel(value[0], tx->ni.regs + 0x66ac);
	writel(value[1], tx->ni.regs + 0x66a8);
	return ni_access(tx, 0x66a4, BIT(31) | BIT(30) | index);
}

static int ni_uplink_stop(struct rtl_ax_tx *tx)
{
	void *memory;
	u32 status;
	int ret;
	if (!tx->ni.uplink_memory) return 0;
	memory = rtl_ax_rx_pe_memory(tx->rx);
	put_unaligned_le32(0, memory + PE_PROBE_MAILBOX + 372);
	dma_wmb();
	ret = rtl_ax_tx_pause(tx);
	if (ret) return ret;
	ret = readl_poll_timeout(tx->ni.vp + 0x1c, status, (status & RTL_AX_NI_TX_MASK) == (readl(tx->ni.vp + 0x18) & RTL_AX_NI_TX_MASK), 10, 100000);
	if (ret) { rtl_ax_tx_open(tx); return ret; }
	writel(0, tx->ni.vp);
	put_unaligned_le32(0, memory + PE_PROBE_MAILBOX + 352);
	put_unaligned_le32(get_unaligned_le32(memory + PE_PROBE_MAILBOX + 360),
		memory + PE_PROBE_MAILBOX + 364);
	writel(tx->ni.vp_saved[1], tx->ni.vp + 8);
	writel(tx->ni.vp_saved[2], tx->ni.vp + 12);
	writel(BIT(31) | BIT(30), tx->ni.vp + 4);
	ret = readl_poll_timeout(tx->ni.vp + 4, status, !(status & BIT(31)), 1, 100000);
	if (ret) { rtl_ax_tx_open(tx); return ret; }
	writel(tx->ni.vp_saved[3], tx->ni.vp + 0x64);
	writel(tx->ni.vp_saved[4], tx->ni.vp + 0x78);
	writel((readl(tx->ni.dma + 0x14) & ~(3U << (tx->ni.vp_index * 2))) | (tx->ni.vp_saved[0] & (3U << (tx->ni.vp_index * 2))), tx->ni.dma + 0x14);
	dma_free_coherent(tx->dma_device, RTL_AX_NI_TX_BYTES, tx->ni.uplink_memory, tx->ni.uplink_dma);
	tx->ni.uplink_memory = NULL;
	dma_wmb();
	rtl_ax_tx_open(tx);
	return 0;
}

static void uplink_counter_start(struct rtl_ax_tx *tx, u32 start)
{
	u8 *memory = rtl_ax_rx_pe_memory(tx->rx);
	u32 old_read = get_unaligned_le32(memory + PE_PROBE_MAILBOX + 364);
	/* A queue-base reload starts a new cursor epoch. Preserve each held
	 * payload's distance from the old read cursor in the new epoch. */
	for (unsigned int i = 0; i < 2; i++) {
		u8 *held = memory + 0x17810 + i * 8;
		if (get_unaligned_le32(held))
			put_unaligned_le32((start - (old_read - get_unaligned_le32(held + 4))) & 0x7fffffff, held + 4);
	}
	put_unaligned_le32(start, memory + PE_PROBE_MAILBOX + 360);
	put_unaligned_le32(start, memory + PE_PROBE_MAILBOX + 364);
	put_unaligned_le32(start, memory + PE_PROBE_MAILBOX + 472);
}

static int ni_uplink_start(struct rtl_ax_tx *tx, unsigned int wifi_id)
{
	u8 *mailbox = rtl_ax_rx_pe_memory(tx->rx) + PE_PROBE_MAILBOX;
	u32 status;
	int ret;

	if (tx->ni.uplink_memory) return 0;
	ret = rtl_ax_tx_pause(tx);
	if (ret) return ret;
	tx->ni.uplink_memory = dma_alloc_coherent(tx->dma_device, RTL_AX_NI_TX_BYTES, &tx->ni.uplink_dma, GFP_KERNEL);
	if (!tx->ni.uplink_memory) { rtl_ax_tx_open(tx); return -ENOMEM; }
	memset(tx->ni.uplink_memory, 0, RTL_AX_NI_TX_BYTES);
	tx->ni.vp_saved[0] = readl(tx->ni.dma + 0x14);
	tx->ni.vp_saved[1] = readl(tx->ni.vp + 8);
	tx->ni.vp_saved[2] = readl(tx->ni.vp + 12);
	tx->ni.vp_saved[3] = readl(tx->ni.vp + 0x64);
	tx->ni.vp_saved[4] = readl(tx->ni.vp + 0x78);
	writel(0, tx->ni.vp + 0x64);
	writel(2, tx->ni.vp + 8);
	writel(lower_32_bits(tx->ni.uplink_dma) | RTL_AX_NI_TX_DEPTH, tx->ni.vp + 12);
	writel(BIT(31) | BIT(30), tx->ni.vp + 4);
	ret = readl_poll_timeout(tx->ni.vp + 4, status, !(status & BIT(31)), 1, 100000);
	if (ret) { ni_uplink_stop(tx); return ret; }
	writel((tx->ni.vp_saved[0] & ~(3U << (tx->ni.vp_index * 2))) |
		(1U << (tx->ni.vp_index * 2)), tx->ni.dma + 0x14);
	writel(0, tx->ni.vp + 0x78);
	put_unaligned_le32(tx->ni.uplink_dma, mailbox + 356);
	uplink_counter_start(tx, readl(tx->ni.vp + 0x1c) & RTL_AX_NI_TX_MASK);
	put_unaligned_le32(wifi_id, mailbox + 368);
	put_unaligned_le32(1, mailbox + 372);
	rtl_ax_rx_uplink_peer(tx->rx, wifi_id, tx->hw);
	dma_wmb();
	put_unaligned_le32(0xb3001100 + tx->ni.vp_index * 0xa0, mailbox + 352);
	writel(1, tx->ni.vp);
	rtl_ax_tx_open(tx);
	return 0;
}

static bool ni_epps_empty(struct rtl_ax_tx *tx)
{
	unsigned int cos;
	for (cos = 0; cos < 8; cos++)
		if (readl(tx->ni.regs + (0x7000 + tx->ni.cpu * 32) + cos * 4) != readl(tx->ni.regs + tx->ni.registers[11] + cos * 4)) return false;
	return true;
}

static void ni_restore_registers(struct rtl_ax_tx *tx)
{
	unsigned int i;
	for (i = 0; i < 14; i++)
		if (tx->ni.registers[i] != 0x6108)
			writel(tx->ni.registers[i] == tx->ni.registers[11] ? readl(tx->ni.regs + (0x7000 + tx->ni.cpu * 32)) : tx->ni.saved[i], tx->ni.regs + tx->ni.registers[i]);
	for (i = 0; i < 8; i++) {
		writel(tx->ni.arb[i], tx->ni.regs + 0x1658);
		ni_access(tx, 0x1654, BIT(31) | BIT(30) | (i * 64 + 16 + tx->ni.cpu));
	}
	for (i = 0; i < 8; i++) {
		writel(tx->ni.epp_bases[i], tx->ni.regs + tx->ni.registers[12] + i * 4);
		writel(tx->ni.epp_eq[i], tx->ni.regs + tx->ni.registers[10] + i * 4);
		writel(readl(tx->ni.regs + (0x7000 + tx->ni.cpu * 32) + i * 4), tx->ni.regs + tx->ni.registers[11] + i * 4);
	}
	attr_write(tx, tx->ni.eq, tx->ni.attrs[0]);
	attr_write(tx, 48 + tx->ni.cpu, tx->ni.attrs[1]);
	/* ES_CTRL also owns CPU0/LAN/PON. Restore only this radio's CPU enable bit. */
	writel((readl(tx->ni.regs + 0x6108) & ~BIT(tx->ni.cpu)) |
		(tx->ni.saved[13] & BIT(tx->ni.cpu)), tx->ni.regs + 0x6108);
}

static int ni_test_release(struct rtl_ax_tx *tx)
{
	unsigned int i;
	u32 status;
	int ret;

	ret = ni_uplink_stop(tx);
	if (ret) return ret;
	if (tx->ni.active) {
		/* Stop new EPP publication before changing ring bases and pool maps. */
		writel(readl(tx->ni.regs + 0x6108) & ~BIT(tx->ni.cpu), tx->ni.regs + 0x6108);
		readl(tx->ni.regs + 0x6108);
		if (!ni_epps_empty(tx)) return -EBUSY;
		if (get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 152)) return -EBUSY;
		/* Empty EPPs do not account for WPs awaiting RPQ or EQ return. */
		if (get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 120)) return -EBUSY;
	}
	if (tx->ni.vp_active) {
		writel(0, tx->ni.vp);
		ret = readl_poll_timeout(tx->ni.vp + 0x1c, status, readl(tx->ni.vp + 0x18) == status, 1, 100000);
		if (ret) return ret;
		writel(tx->ni.vp_saved[1], tx->ni.vp + 8);
		writel(tx->ni.vp_saved[2], tx->ni.vp + 12);
		writel(BIT(31) | BIT(30), tx->ni.vp + 4);
		readl_poll_timeout(tx->ni.vp + 4, status, !(status & BIT(31)), 1, 100000);
		writel(tx->ni.vp_saved[3], tx->ni.vp + 0x64);
		writel(tx->ni.vp_saved[4], tx->ni.vp + 0x78);
		writel((readl(tx->ni.dma + 0x14) & ~(3U << (tx->ni.vp_index * 2))) |
			(tx->ni.vp_saved[0] & (3U << (tx->ni.vp_index * 2))), tx->ni.dma + 0x14);
	}
	ret = rtl_ax_tx_pause(tx);
	if (ret) return ret;
	ret = rtl_ax_rx_pe_rpq_stop(tx->rx);
	if (ret) return ret;
	ni_ps_retire(tx);
	pe_event_release(tx);
	cancel_work_sync(&tx->ni.ps_work);
	if (get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 96)) {
		for (i = 0; i < RTL_AX_TX_RINGS; i++)
			tx->producer[i] = get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 128 + i * 4);
		put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 96);
	}
	dma_wmb();
	if (tx->ni.active) ni_restore_registers(tx);
	rtl_ax_tx_open(tx);
	if (tx->ni.test_memory) dma_free_coherent(tx->dma_device, TEST_MEMORY_BYTES, tx->ni.test_memory, tx->ni.test_dma);
	if (tx->ni.dma) iounmap(tx->ni.dma);
	if (tx->ni.regs) iounmap(tx->ni.regs);
	tx->ni.active = tx->ni.vp_active = false;
	tx->ni.test_memory = NULL; tx->ni.regs = tx->ni.dma = tx->ni.vp = NULL;
	return 0;
}

static int ni_test_prepare(struct rtl_ax_tx *tx, unsigned int macid)
{
	static const u32 eq_attr[] = { 0, 0 };
	static const u32 epp_attr[] = { 0x0a008060, 0 };
	unsigned int i;
	int ret;

	tx->ni.regs = ioremap(0x4f4300000ULL, 0x10000);
	tx->ni.dma = ioremap(0x4f7001000ULL, 0x1000);
	if (!tx->ni.regs || !tx->ni.dma) return -ENOMEM;
	tx->ni.vp = tx->ni.dma + 0x100 + tx->ni.vp_index * 0xa0;
	pr_info("native PE packet pre EQ%u=%08x/%08x EPP%u=%u/%u base=%08x VP%u=%08x/%u/%u\n",
		tx->ni.eq, readl(tx->ni.regs + tx->ni.registers[0]), readl(tx->ni.regs + tx->ni.registers[0] + 4), tx->ni.cpu * 8, readl(tx->ni.regs + tx->ni.registers[11]), readl(tx->ni.regs + (0x7000 + tx->ni.cpu * 32)),
		readl(tx->ni.regs + tx->ni.registers[12]), tx->ni.vp_index, readl(tx->ni.vp), readl(tx->ni.vp + 0x1c), readl(tx->ni.vp + 0x18));
	if (!(readl(tx->ni.regs + tx->ni.registers[0]) & 1) || readl(tx->ni.regs + tx->ni.registers[0] + 4) != (BUFFER_COUNT << 16 | (RTL9617C_WIFI_BID_START + tx->pe.slot * BUFFER_COUNT)) ||
	    readl(tx->ni.regs + tx->ni.registers[11]) != readl(tx->ni.regs + (0x7000 + tx->ni.cpu * 32)) ||
	    readl(tx->ni.vp) || readl(tx->ni.vp + 0x1c) != readl(tx->ni.vp + 0x18)) return -EBUSY;
	for (i = 0; i < 14; i++) tx->ni.saved[i] = readl(tx->ni.regs + tx->ni.registers[i]);
	for (i = 0; i < 8; i++) {
		ret = ni_access(tx, 0x1654, BIT(31) | (i * 64 + 16 + tx->ni.cpu));
		if (ret) return ret;
		tx->ni.arb[i] = readl(tx->ni.regs + 0x1658);
	}
	for (i = 0; i < 2; i++) {
		ret = ni_access(tx, 0x66a4, BIT(31) | (i ? 48 + tx->ni.cpu : tx->ni.eq));
		if (ret) return ret;
		tx->ni.attrs[i][0] = readl(tx->ni.regs + 0x66ac); tx->ni.attrs[i][1] = readl(tx->ni.regs + 0x66a8);
	}
	tx->ni.active = true;
	rtl_ax_tx_ps_clock(tx, ktime_get_boottime_seconds());
	memset(tx->pe.memory + 0x11000, 0, 0xc00);
	memset(tx->pe.memory + 0x13000, 0, 0x4000);
	memset(tx->pe.memory + 0x19000, 0, 0x400);
	memset(tx->pe.memory + 0x18000, 0, 0x1000);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 680);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 684);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 152);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 224);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 228);
	writel(tx->ni.eq | 15 << 4, tx->ni.regs + tx->ni.registers[5]);
	writel(tx->ni.profile, tx->ni.regs + tx->ni.registers[6]);
	writel(RTL_QM_PACKET_LAYOUT, tx->ni.regs + tx->ni.registers[7]);
	writel(0, tx->ni.regs + tx->ni.registers[8]);
	writel((readl(tx->ni.regs + tx->ni.registers[9]) & GENMASK(31, 29)) |
	       (RING_BYTES / 16) << 8 | 0xf2, tx->ni.regs + tx->ni.registers[9]);
	for (i = 0; i < 8; i++) {
		tx->ni.epp_bases[i] = readl(tx->ni.regs + tx->ni.registers[12] + i * 4);
		tx->ni.epp_eq[i] = readl(tx->ni.regs + tx->ni.registers[10] + i * 4);
		writel(tx->ni.profile, tx->ni.regs + tx->ni.registers[10] + i * 4);
		writel(readl(tx->ni.regs + (0x7000 + tx->ni.cpu * 32) + i * 4), tx->ni.regs + tx->ni.registers[11] + i * 4);
		put_unaligned_le32(readl(tx->ni.regs + tx->ni.registers[11] + i * 4), tx->pe.memory + EPP_CONSUMERS + i * 4);
	}
	put_unaligned_le32(0x7000 + tx->ni.cpu * 32, tx->pe.memory + PE_PROBE_MAILBOX + 456);
	put_unaligned_le32(0x7100 + tx->ni.cpu * 32, tx->pe.memory + PE_PROBE_MAILBOX + 460);
	put_unaligned_le32(0x6368 + tx->ni.cpu * 8, tx->pe.memory + PE_PROBE_MAILBOX + 464);
	put_unaligned_le32(tx->ni.eq, tx->pe.memory + PE_PROBE_MAILBOX + 468);
	put_unaligned_le32(RTL9617C_WIFI_EPP_SHIFT, tx->pe.memory + PE_PROBE_MAILBOX + 668);
	put_unaligned_le32(readl(tx->ni.regs + tx->ni.registers[12]), tx->pe.memory + PE_PROBE_MAILBOX + 100);
	put_unaligned_le32(readl(tx->ni.regs + tx->ni.registers[11]), tx->pe.memory + PE_PROBE_MAILBOX + 104);
	put_unaligned_le32(macid, tx->pe.memory + PE_PROBE_MAILBOX + 108);
	ret = attr_write(tx, tx->ni.eq, eq_attr); if (ret) return ret;
	ret = attr_write(tx, 48 + tx->ni.cpu, epp_attr); if (ret) return ret;
	for (i = 0; i < 8; i++) {
		writel(9, tx->ni.regs + 0x1658);
		ret = ni_access(tx, 0x1654, BIT(31) | BIT(30) | (i * 64 + 16 + tx->ni.cpu));
		if (ret) return ret;
	}
	for (i = 0; i < RTL_AX_TX_RINGS; i++)
		put_unaligned_le32(tx->producer[i], tx->pe.memory + PE_PROBE_MAILBOX + 128 + i * 4);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + RTL_AX_TX_PUBLISH_WRITE * 4);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + RTL_AX_TX_PUBLISH_READ * 4);
	put_unaligned_le32(1, tx->pe.memory + PE_PROBE_MAILBOX + 96);
	ret = pe_event_prepare(tx);
	if (ret) return ret;
	ret = rtl_ax_rx_pe_rpq_start(tx->rx, tx->pe.memory, lower_32_bits(tx->pe.dma),
		pe_tx_program, sizeof(pe_tx_program));
	if (ret) return ret;
	dma_wmb();
	writel(readl(tx->ni.regs + 0x6108) | BIT(tx->ni.cpu), tx->ni.regs + 0x6108);
	return 0;
}

static int ni_test_remove(struct rtl_ax_tx *tx)
{
	int ret;
	u32 status;
	unsigned int i;
	if (tx->ni.route_dev) rtl_ax_tx_ni_route(tx, tx->ni.route_dev, 0);
	if (!tx->ni.active) return 0;
	ret = ni_test_release(tx);
	if (!ret) return rtl_ax_tx_pause(tx);
	/* PCIe DMA is already closed by radio teardown. Stop NI publication
	 * before releasing the PE mailbox, its IRQ action, or the EPP memory. */
	writel(readl(tx->ni.regs + 0x6108) & ~BIT(tx->ni.cpu), tx->ni.regs + 0x6108);
	readl(tx->ni.regs + 0x6108);
	writel(0, tx->ni.vp);
	readl(tx->ni.vp);
	ret = rtl_ax_tx_pause(tx);
	if (ret) return ret;
	pe_event_release(tx);
	cancel_work_sync(&tx->ni.ps_work);
	ret = rtl_ax_rx_pe_rpq_stop(tx->rx);
	if (ret) return ret;
	writel(tx->ni.vp_saved[1], tx->ni.vp + 8); writel(tx->ni.vp_saved[2], tx->ni.vp + 12);
	writel(BIT(31) | BIT(30), tx->ni.vp + 4);
	ret = readl_poll_timeout(tx->ni.vp + 4, status, !(status & BIT(31)), 1, 100000);
	if (ret) return ret;
	writel(tx->ni.vp_saved[3], tx->ni.vp + 0x64); writel(tx->ni.vp_saved[4], tx->ni.vp + 0x78);
	writel((readl(tx->ni.dma + 0x14) & ~(3U << (tx->ni.vp_index * 2))) | (tx->ni.vp_saved[0] & (3U << (tx->ni.vp_index * 2))), tx->ni.dma + 0x14);
	for (i = 0; i < 128; i++) {
		u32 address = get_unaligned_le32(tx->pe.memory + 0xc400 + i * 8);
		while (address) {
			u32 next;
			dma_sync_single_for_cpu(tx->dma_device, address, 4, DMA_FROM_DEVICE);
			next = get_unaligned_le32(phys_to_virt(address));
			ret = readl_poll_timeout(tx->ni.regs + (0x6368 + tx->ni.cpu * 8), status, status & BIT(31), 1, 100000);
			if (ret) return ret;
			writel(address | tx->ni.eq, tx->ni.regs + (0x636c + tx->ni.cpu * 8));
			put_unaligned_le32(next, tx->pe.memory + 0xc400 + i * 8);
			address = next;
		}
	}
	/* Queued BIDs have no WP owner yet; their links remain in BID headroom. */
	for (i = 0; i < 128 * 8; i++) {
		u32 address = get_unaligned_le32(tx->pe.memory + 0x15000 + i * 4);
		while (address) {
			u32 next;
			dma_sync_single_for_cpu(tx->dma_device, address, 4, DMA_FROM_DEVICE);
			next = get_unaligned_le32(phys_to_virt(address));
			ret = readl_poll_timeout(tx->ni.regs + (0x6368 + tx->ni.cpu * 8), status, status & BIT(31), 1, 100000);
			if (ret) return ret;
			writel(address | tx->ni.eq, tx->ni.regs + (0x636c + tx->ni.cpu * 8));
			put_unaligned_le32(next, tx->pe.memory + 0x15000 + i * 4);
			address = next;
		}
	}
	ni_restore_registers(tx);
	if (tx->ni.route_dev) { dev_put(tx->ni.route_dev); tx->ni.route_dev = NULL; }
	if (tx->ni.test_memory) dma_free_coherent(tx->dma_device, TEST_MEMORY_BYTES, tx->ni.test_memory, tx->ni.test_dma);
	iounmap(tx->ni.dma); iounmap(tx->ni.regs);
	tx->ni.active = tx->ni.vp_active = false;
	tx->ni.test_memory = NULL; tx->ni.regs = tx->ni.dma = tx->ni.vp = NULL;
	return 0;
}

int rtl_ax_tx_ni_route(struct rtl_ax_tx *tx, struct net_device *netdev, unsigned int macid)
{
	u32 status;
	unsigned int i;
	int ret = 0;

	if (!tx->pe_active || macid >= 128) return -EINVAL;
	mutex_lock(&ni_test_lock);
	if (!macid) {
		if (!tx->ni.route_dev) goto unlock;
		for (i = 1; i < 128; i++) {
			if (!tx->ni.route_peers[i]) continue;
			put_unaligned_le32(0, tx->pe.memory + 0x11a00 + i * 4);
			dma_wmb();
			ret = rtl9617c_wifi_peer_route(tx->ni.route_dev, tx->pe.memory + 0xe400 + i * 8, false);
			if (ret) goto unlock;
		}
		/* FE no longer submits this peer. Return its queued, unsent BIDs even
		 * when the disconnected STA remains asleep; submitted WPs await RPQ. */
		ret = rtl_ax_tx_pause(tx);
		if (ret) goto unlock;
		put_unaligned_le32(128, tx->pe.memory + PE_PROBE_MAILBOX + 248);
		dma_wmb();
		rtl_ax_tx_open(tx);
		/* Sleeping MAC queues retain submitted WPs until PKT_DROP releases them. */
		for (i = 1; i < 128; i++) {
			if (!tx->ni.route_peers[i]) continue;
			ret = rtl_ax_mac_drop_peer(tx->pdev, i);
			if (ret) goto unlock;
		}
		ret = read_poll_timeout(get_unaligned_le32, status,
			!status && !get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 152) && ni_epps_empty(tx),
			100, 2000000, false, tx->pe.memory + PE_PROBE_MAILBOX + 120);
		if (ret) goto unlock;
		for (i = 1; i < 128; i++) {
			if (tx->ni.route_peers[i]) ni_test_buffered(tx, i, false);
			WRITE_ONCE(tx->ni.route_peers[i], false);
			WRITE_ONCE(tx->peer_offloaded[i], false);
			put_unaligned_le32(0, tx->pe.memory + 0x11a00 + i * 4);
		}
		WRITE_ONCE(tx->ni.route_macid, 0);
		cancel_work_sync(&tx->ni.ps_work);
		ret = ni_test_release(tx);
		if (ret) goto unlock;
		dev_put(tx->ni.route_dev); tx->ni.route_dev = NULL;
		goto unlock;
	}
	if (tx->ni.active || tx->ni.route_dev) { ret = -EBUSY; goto unlock; }
	if (!(get_unaligned_le32(tx->pe.memory + 0xd000 + macid * 32 + 16) & BIT(16))) {
		ret = -ENOLINK; goto unlock;
	}
	ret = wait_event_timeout(tx->key_flush_wait, READ_ONCE(tx->occupied) <= 96, msecs_to_jiffies(1000)) ? 0 : -ETIMEDOUT;
	if (ret) goto unlock;
	ret = rtl_ax_tx_pause(tx);
	if (ret) goto unlock;
	ret = ni_test_prepare(tx, macid);
	if (ret) { ni_test_release(tx); rtl_ax_tx_open(tx); goto unlock; }
	for (i = 28; i < 31; i++) put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + i * 4);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 248);
	tx->ni_reports = tx->ni_failed = 0;
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 300);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 304);
	dev_hold(netdev); tx->ni.route_dev = netdev;
	WRITE_ONCE(tx->ni.route_macid, macid);
	memset(tx->ni.route_tim, 0, sizeof(tx->ni.route_tim));
	rtl_ax_tx_open(tx);
	schedule_work(&tx->ni.ps_work);
	for (i = 1; i < 128; i++) {
		if (!tx->peer_authorized[i] ||
		    !(get_unaligned_le32(tx->pe.memory + 0xd000 + i * 32 + 16) & BIT(16))) continue;
		put_unaligned_le32(1, tx->pe.memory + 0x11a00 + i * 4);
		dma_wmb();
		WRITE_ONCE(tx->ni.route_peers[i], true);
		ret = rtl9617c_wifi_peer_route(netdev, tx->pe.memory + 0xe400 + i * 8, 16 + tx->ni.cpu);
		if (ret) goto unlock;
	}
unlock:
	mutex_unlock(&ni_test_lock);
	return ret;
}

int rtl_ax_tx_ni_peer(struct rtl_ax_tx *tx, struct net_device *netdev, unsigned int macid)
{
	int id, ret;
	if (!tx->pe_active || (READ_ONCE(tx->peer_offloaded[macid]) &&
	    READ_ONCE(tx->ni.route_peers[macid]))) return 0;
	if (!(get_unaligned_le32(tx->pe.memory + 0xd000 + macid * 32 + 16) & BIT(16))) return -ENOLINK;
	id = rtl9617c_wifi_id(netdev);
	if (id < 0) return id;
	if (!tx->ni.active) {
		ret = rtl_ax_tx_ni_route(tx, netdev, macid);
		if (ret) return ret;
	}
	mutex_lock(&ni_test_lock);
	ret = ni_uplink_start(tx, id);
	if (ret) { mutex_unlock(&ni_test_lock); return ret; }
	put_unaligned_le32(1, tx->pe.memory + 0x11a00 + macid * 4);
	dma_wmb();
	/* Own the prepared path before FE can publish even its first rule.
	 * A failed publish retains admission and is retried by offload_work. */
	WRITE_ONCE(tx->ni.route_peers[macid], true);
	ret = rtl9617c_wifi_peer_route(netdev, tx->pe.memory + 0xe400 + macid * 8, 16 + tx->ni.cpu);
	if (!ret) {
		WRITE_ONCE(tx->peer_offloaded[macid], true);
		rtl_ax_rx_uplink_enable(tx->rx, macid);
	}
	mutex_unlock(&ni_test_lock);
	return ret ? ret : 1;
}

static void ni_peer_fence(struct rtl_ax_tx *tx)
{
	u32 mask = 0;

	/* FE withdrawal is complete and PE is paused. Fence the existing entries;
	 * other stations may keep appending after these producer positions. */
	for (unsigned int cos = 0; cos < 8; cos++) {
		u32 producer = readl(tx->ni.regs + 0x7000 + tx->ni.cpu * 32 + cos * 4) & (RING_BYTES - 1);
		put_unaligned_le32(producer, tx->pe.memory + EPP_RETIRE_TARGETS + cos * 4);
		if (producer != get_unaligned_le32(tx->pe.memory + EPP_CONSUMERS + cos * 4)) mask |= BIT(cos);
	}
	dma_wmb();
	put_unaligned_le32(mask, tx->pe.memory + PE_PROBE_MAILBOX + 684);
}

static bool ni_peer_idle(struct rtl_ax_tx *tx, unsigned int macid)
{
	unsigned int i;
	if (get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 684)) return false;
	dma_rmb();
	if (get_unaligned_le32(tx->pe.memory + 0x11800 + macid * 4)) return false;
	for (i = 0; i < 128; i++)
		if (get_unaligned_le32(tx->pe.memory + 0xc400 + i * 8) &&
		    ((get_unaligned_le32(tx->pe.memory + 0xc404 + i * 8) >> 8) & 127) == macid)
			return false;
	return true;
}

int rtl_ax_tx_rx_retire(struct rtl_ax_tx *tx, unsigned int macid)
{
	bool running = READ_ONCE(tx->running);
	int ret;

	if (!tx->ni.active) return 0;
	rtl_ax_rx_uplink_disable(tx->rx, macid);
	ret = rtl_ax_tx_pause(tx);
	if (ret) {
		/* No ownership changed: cancel this runtime pause, not a prior RFK stop. */
		if (running) rtl_ax_tx_open(tx);
		return ret;
	}
	rtl_ax_rx_uplink_retire(tx->rx, macid);
	WRITE_ONCE(tx->peer_offloaded[macid], false);
	if (running) rtl_ax_tx_open(tx);
	return 0;
}

static int ni_retire_peer(struct rtl_ax_tx *tx, unsigned int macid, bool keep_rx)
{
	unsigned int i;
	u32 status;
	int ret;
	if (!READ_ONCE(tx->ni.route_peers[macid]) && !rtl_ax_rx_uplink_active(tx->rx, macid)) return 0;
	if (!keep_rx) rtl_ax_rx_uplink_disable(tx->rx, macid);
	/* Key/BA retirement must stop admission before attempting FE withdrawal. */
	put_unaligned_le32(0, tx->pe.memory + 0x11a00 + macid * 4);
	dma_wmb();
	for (i = 1; i < 128; i++)
		if (i != macid && (READ_ONCE(tx->ni.route_peers[i]) ||
		    rtl_ax_rx_uplink_active(tx->rx, i))) break;
	if (!keep_rx && i == 128) return rtl_ax_tx_ni_route(tx, tx->ni.route_dev, 0);
	mutex_lock(&ni_test_lock);
	ret = rtl9617c_wifi_peer_route(tx->ni.route_dev, tx->pe.memory + 0xe400 + macid * 8, false);
	if (ret) goto unlock;
	ret = rtl_ax_tx_pause(tx);
	if (ret) goto unlock;
	if (!keep_rx) rtl_ax_rx_uplink_retire(tx->rx, macid);
	ni_peer_fence(tx);
	put_unaligned_le32(macid, tx->pe.memory + PE_PROBE_MAILBOX + 248);
	dma_wmb();
	rtl_ax_tx_open(tx);
	if (ret) goto unlock;
	/* PE admission is closed and its last submission has finished; flush before waiting for RPQ. */
	ret = rtl_ax_mac_drop_peer(tx->pdev, macid);
	if (ret) goto unlock;
	ret = read_poll_timeout(ni_peer_idle, status, status, 100, 2000000, false, tx, macid);
	if (ret) goto unlock;
	WRITE_ONCE(tx->ni.route_peers[macid], false);
	if (!keep_rx) WRITE_ONCE(tx->peer_offloaded[macid], false);
	put_unaligned_le32(0, tx->pe.memory + 0x11a00 + macid * 4);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 248);
	dma_wmb();
	ni_test_buffered(tx, macid, false);
	tx->ni.route_tim[macid] = 0;
unlock:
	mutex_unlock(&ni_test_lock);
	return ret;
}

int rtl_ax_tx_ni_retire_peer(struct rtl_ax_tx *tx, unsigned int macid)
{
	return ni_retire_peer(tx, macid, false);
}

int rtl_ax_tx_ni_retire_ba(struct rtl_ax_tx *tx, unsigned int macid)
{
	/* A TX aggregation change does not invalidate RX's key, PN or BA window. */
	return ni_retire_peer(tx, macid, true);
}

int rtl_ax_tx_ni_test(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid)
{
	u32 status;
	unsigned int first, sequence, i;
	u8 destination[6];
	u64 first_report_us = 0;
	u64 traffic_begin = 0, traffic_elapsed;
	u64 first_begin = 0, start_real = 0;
	u32 first_indices[6] = {}, first_sent[6] = {}, first_recycled[6] = {};
	int ret;

	if (!tx->pe_active || macid >= 128 || tid > 7) return -EINVAL;
	if (!macid) {
		mutex_lock(&ni_test_lock);
		ret = get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 120) ? -EBUSY :
			tx->ni.active ? ni_test_release(tx) : 0;
		mutex_unlock(&ni_test_lock);
		return ret;
	}
	if (!(get_unaligned_le32(tx->pe.memory + 0xd000 + macid * 32 + 16) & BIT(16))) return -ENOLINK;
	mutex_lock(&ni_test_lock);
	if (tx->ni.active) {
		if (get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 120)) { ret = -EBUSY; goto unlock; }
		ret = ni_test_release(tx);
		if (ret) goto unlock;
	}
	pr_info("native PE NI-radio peer_start_asleep=%u sleep_events=%llu wake_events=%llu\n",
		tx->peer_asleep[macid], tx->peer_sleep_events, tx->peer_wake_events);
	memcpy(destination, tx->pe.memory + 0xe400 + macid * 8, 6);
	ret = wait_event_timeout(tx->key_flush_wait, READ_ONCE(tx->occupied) <= 96, msecs_to_jiffies(1000)) ? 0 : -ETIMEDOUT;
	if (ret) goto unlock;
	ret = rtl_ax_tx_pause(tx);
	if (ret) goto unlock;
	tx->ni.test_memory = dma_alloc_coherent(tx->dma_device, TEST_MEMORY_BYTES, &tx->ni.test_dma, GFP_KERNEL);
	if (!tx->ni.test_memory) { ret = -ENOMEM; goto out; }
	ret = ni_test_prepare(tx, macid);
	if (ret) goto out;
	put_unaligned_le32(1, tx->pe.memory + 0x11a00 + macid * 4);
	WRITE_ONCE(tx->ni.route_peers[macid], true);
	for (i = 28; i < 31; i++) put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + i * 4);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 248);
	tx->ni_reports = tx->ni_failed = 0;
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 300);
	put_unaligned_le32(0, tx->pe.memory + PE_PROBE_MAILBOX + 304);
	tx->ni_first_report_ns = 0;
	first = readl(tx->ni.vp + 0x1c) & RTL_AX_NI_TX_MASK;
	tx->ni.vp_saved[0] = readl(tx->ni.dma + 0x14); tx->ni.vp_saved[1] = readl(tx->ni.vp + 8);
	tx->ni.vp_saved[2] = readl(tx->ni.vp + 12); tx->ni.vp_saved[3] = readl(tx->ni.vp + 0x64); tx->ni.vp_saved[4] = readl(tx->ni.vp + 0x78);
	tx->ni.vp_active = true;
	writel(0, tx->ni.vp + 0x64);
	writel(2, tx->ni.vp + 8); writel((u32)tx->ni.test_dma + 0x2000 + RTL_AX_NI_TX_DEPTH, tx->ni.vp + 12);
	writel(BIT(31) | BIT(30), tx->ni.vp + 4);
	ret = readl_poll_timeout(tx->ni.vp + 4, status, !(status & BIT(31)), 1, 100000);
	if (ret) goto out;
	writel((tx->ni.vp_saved[0] & ~(3U << (tx->ni.vp_index * 2))) | (1U << (tx->ni.vp_index * 2)), tx->ni.dma + 0x14);
	writel(BIT(9), tx->ni.vp + 0x78);
	writel(1, tx->ni.vp);
	WRITE_ONCE(tx->ni.route_macid, macid);
	memset(tx->ni.route_tim, 0, sizeof(tx->ni.route_tim));
	rtl_ax_tx_open(tx);
	traffic_begin = ktime_get_ns();
	for (sequence = 0; sequence < PACKETS;) {
		unsigned int batch = min(sequence ? 32U : 1U, PACKETS - sequence);
		u64 begin;
		for (i = 0; i < batch; i++) {
			unsigned int slot = (first + sequence + i) & RTL_AX_NI_TX_MASK;
			u8 *data = tx->ni.test_memory + 0x4000 + slot * 2048;
			put_unaligned_le32(0, data); put_unaligned_le32(TEST_FRAME_BYTES, data + 4);
			put_unaligned_le32(BIT(13), data + 8);
			put_unaligned_le32(TEST_FRAME_BYTES << 15 | 16U << 9 | (16U + tx->ni.cpu) << 3 | BIT(29) | tid, data + 12);
			memcpy(data + 16, destination, 6);
			memcpy(data + 22, "\x02\x57\x46\x4e\x45\x01\x88\xb5", 8);
			memcpy(data + 30, "TYFPE-NI", 8);
			put_unaligned_be32(sequence + i, data + 38);
			memset(data + 42, (sequence + i) & 255, TEST_FRAME_BYTES - 26);
			put_unaligned_le32((u32)tx->ni.test_dma + 0x4000 + slot * 2048, tx->ni.test_memory + 0x2000 + slot * 8);
			put_unaligned_le32(0x61000002U | ((TEST_FRAME_BYTES + 16) << 8), tx->ni.test_memory + 0x2004 + slot * 8);
		}
		dma_wmb();
		begin = ktime_get_ns();
		if (!sequence) { first_begin = begin; start_real = ktime_get_real_ns(); }
		writel((first + sequence + batch) & RTL_AX_NI_TX_MASK, tx->ni.vp + 0x18); writel(1, tx->ni.vp + 0x68);
		ret = readl_poll_timeout(tx->ni.vp + 0x1c, status, (status & RTL_AX_NI_TX_MASK) == ((first + sequence + batch) & RTL_AX_NI_TX_MASK), 10, 100000);
		if (ret) break;
		if (!sequence) {
			for (i = 0; i < 6; i++) {
				first_indices[i] = tx->io->read32(tx->io->ctx, 0x121c);
				first_sent[i] = get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 112);
				first_recycled[i] = get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 116);
				if (first_recycled[i]) break;
				if (i < 5) msleep(100);
			}
			ret = i < 6 ? 0 : -ETIMEDOUT;
		} else
			ret = read_poll_timeout(get_unaligned_le32, status, status == sequence + batch,
				10, 500000, false, tx->pe.memory + PE_PROBE_MAILBOX + 116);
		if (ret) break;
		if (!sequence) first_report_us = div_u64(ktime_get_ns() - begin, 1000);
		sequence += batch;
	}
	traffic_elapsed = ktime_get_ns() - traffic_begin;
	pr_info("native PE NI-radio stress frames=%u bytes=%u elapsed_ns=%llu Kbps=%llu\n",
		sequence, sequence * TEST_FRAME_BYTES, traffic_elapsed,
		div_u64((u64)sequence * TEST_FRAME_BYTES * 8000, div_u64(traffic_elapsed, 1000)));
	pr_info("native PE NI-radio start_real_ns=%llu first_ARM_RPQ_us=%lld samples_RPQ=%08x,%08x,%08x,%08x,%08x,%08x sent=%u,%u,%u,%u,%u,%u recycled=%u,%u,%u,%u,%u,%u\n",
		start_real, tx->ni_first_report_ns ? (s64)(tx->ni_first_report_ns - first_begin) / 1000 : -1LL,
		first_indices[0], first_indices[1], first_indices[2], first_indices[3], first_indices[4], first_indices[5],
		first_sent[0], first_sent[1], first_sent[2], first_sent[3], first_sent[4], first_sent[5],
		first_recycled[0], first_recycled[1], first_recycled[2], first_recycled[3], first_recycled[4], first_recycled[5]);
	pr_info("native PE NI-radio MACID=%u destination=%pM first_single_packet_RPQ_us=%llu report_cfg=%08x\n",
		macid, destination, first_report_us, tx->io->read32(tx->io->ctx, 0x9414));
	pr_info("native PE NI-radio result=%d sent=%u recycled=%u pending=%u reports=%llu failed=%llu EPP=%u/%u inactive=%u Cause=%08x EPC=%08x\n",
		ret, get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 112),
		get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 116),
		get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 120), ni_reports(tx), ni_failed(tx),
		readl(tx->ni.regs + tx->ni.registers[11]), readl(tx->ni.regs + (0x7000 + tx->ni.cpu * 32)), readl(tx->ni.regs + 0x6328 + tx->ni.eq * 4) & 0x3fff,
		get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 20),
		get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 24));
out:
	/* Keep the probe and EPP ring owned if hardware still holds a radio WP. */
	if (get_unaligned_le32(tx->pe.memory + PE_PROBE_MAILBOX + 120)) goto unlock;
	if (tx->ni.active && readl(tx->ni.regs + (0x7000 + tx->ni.cpu * 32)) != readl(tx->ni.regs + tx->ni.registers[11])) goto unlock;
	WRITE_ONCE(tx->ni.route_macid, 0);
	WRITE_ONCE(tx->ni.route_peers[macid], false);
	cancel_work_sync(&tx->ni.ps_work);
	ni_test_buffered(tx, macid, false);
	{
		int cleanup = ni_test_release(tx);
		if (!ret) ret = cleanup;
	}
unlock:
	mutex_unlock(&ni_test_lock);
	return ret;
}

