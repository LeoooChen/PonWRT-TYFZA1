/* SPDX-License-Identifier: GPL-2.0-only */
/* PN/SN publication uses the existing mac80211 snapshot/sync contract.
 * Storage ownership and RXBD transfer are handled separately in rx.c. */
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
	rx->uplink_open[macid] = false;
	rx->uplink_pending_tids[macid] = 0;
	/* A BA session can survive rekeying; retain its negotiated window. */
	for (unsigned int tid = 0; tid < 16; tid++)
		memset(states + (macid * 16 + tid) * 32, 0, 28);
	memset(rx->uplink_seen[macid], 0, sizeof(rx->uplink_seen[macid]));
	mutex_unlock(&rx->uplink_lock);
}

/* Called only after wiphy-protected verification of the current open peer. */
void rtl_ax_rx_open_peer(struct rtl_ax_rx *rx, unsigned int macid, bool open)
{
 mutex_lock(&rx->uplink_lock);
 if (rx->uplink_open[macid]!=open) {
  put_unaligned_le32(0,rx->uplink_states+0x10000+macid*4);
  dma_wmb();
  rx->uplink_open[macid]=open;
  rx->uplink_pending_tids[macid]=0;
  for(unsigned int tid=0;tid<16;tid++)
   put_unaligned_le32(0,rx->uplink_states+(macid*16+tid)*32);
 }
 mutex_unlock(&rx->uplink_lock);
}

void rtl_ax_rx_ba_window(struct rtl_ax_rx *rx, unsigned int macid,
	unsigned int tid, unsigned int window)
{
	mutex_lock(&rx->uplink_lock);
	put_unaligned_le32(window, rx->uplink_states + (macid * 16 + tid) * 32 + 28);
	dma_wmb();
	mutex_unlock(&rx->uplink_lock);
}

u16 rtl_ax_rx_ba_size(struct rtl_ax_rx *rx, unsigned int macid, unsigned int tid)
{
	return get_unaligned_le32(rx->uplink_states + (macid * 16 + tid) * 32 + 28);
}

bool rtl_ax_rx_uplink_active(struct rtl_ax_rx *rx, unsigned int macid)
{
	return rx->uplink_started &&
		(get_unaligned_le32(rx->uplink_states + 0x10000 + macid * 4) & BIT(31));
}

int rtl_ax_rx_uplink_enable(struct rtl_ax_rx *rx, unsigned int macid, unsigned int wifi_id)
{
	int ret=0;
 if(!macid || macid>=128 || !wifi_id || wifi_id>63)return -EINVAL;
 mutex_lock(&rx->uplink_lock);
 u32 config=get_unaligned_le32(rx->uplink_states+0x10000+macid*4);
 if((config&BIT(31)) && ((config>>8)&63)!=wifi_id){ret=-ESTALE;goto out;}
 if(!rx->uplink_started){ret=-ENETDOWN;goto out;}
	if (rx->keys[macid] || rx->uplink_open[macid]) {
		/* A withdrawn peer may have changed its BA window while ARM owned RX. */
		if (!(get_unaligned_le32(rx->uplink_states + 0x10000 + macid * 4) & BIT(31))) {
			for (unsigned int tid = 0; tid < 16; tid++)
				put_unaligned_le32(0, rx->uplink_states + (macid * 16 + tid) * 32);
			rx->uplink_pending_tids[macid] = 0;
		}
		dma_wmb();
		put_unaligned_le32(BIT(31) | wifi_id << 8 | (rx->keys[macid]?rx->keys[macid]->keyidx:0) |
			(u32)rx->uplink_trigger_tids[macid] << 16 |
			(rx->keys[macid]?(rx->keys[macid]->cipher != WLAN_CIPHER_SUITE_CCMP ? BIT(24) : 0):BIT(25)),
			rx->uplink_states + 0x10000 + macid * 4);
	}
out:
	mutex_unlock(&rx->uplink_lock);
 return ret;
}

void rtl_ax_rx_uplink_disable(struct rtl_ax_rx *rx, unsigned int macid)
{
	mutex_lock(&rx->uplink_lock);
	put_unaligned_le32(0, rx->uplink_states + 0x10000 + macid * 4);
	dma_wmb();
	mutex_unlock(&rx->uplink_lock);
}

void rtl_ax_rx_uplink_start(struct rtl_ax_rx *rx, struct ieee80211_hw *hw)
{
 mutex_lock(&rx->uplink_lock);
 rx->uplink_started=true;rx->hw=hw;
 for(unsigned int macid=1;macid<128;macid++) {
  put_unaligned_le32(0,rx->uplink_states+0x10000+macid*4);
  for(unsigned int tid=0;tid<16;tid++)put_unaligned_le32(0,rx->uplink_states+(macid*16+tid)*32);
 }
 /* Each peer is enabled separately after its own FE publication succeeds. */
 dma_wmb();
 put_unaligned_le32(rx->uplink_states_dma,rx->pe.memory+0x21a8);
 mutex_unlock(&rx->uplink_lock);
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
	u8 *memory = rx->pe.memory;
	unsigned int macid, tid;
	u32 forwarded;
	bool changed, retry = false;

	if (!rx->uplink_started) return;
	mutex_lock(&rx->uplink_lock);
	/* PE publishes this count after all per-TID PN/SN records. During
	 * ARM-only reorder recovery none of those records can have changed. */
	forwarded = get_unaligned_le32(memory + 0x2180);
	changed = forwarded != rx->uplink_forwarded_seen;
	if (changed) dma_rmb();
	local_bh_disable(); rcu_read_lock();
	for (macid = 1; macid < 128; macid++) {
		struct ieee80211_key_conf *key = rx->keys[macid];
		struct ieee80211_sta *sta;
		if (!key && !rx->uplink_open[macid]) continue;
		sta = rtl_ax_lookup_peer(rx->hw, rx->rpq_memory + 0xe400 + macid * 8,
			rx->rpq_memory + RTL_AX_TX_PEER_SELF + macid * 8, macid);
		for (tid = 0; sta && tid < 16; tid++) {
			u8 *state = rx->uplink_states + (macid * 16 + tid) * 32;
			struct rtl_ax_shortcut_seen *seen = &rx->uplink_seen[macid][tid];
			u32 guard, snapshot[6], count;
			u8 pn[6];
			/* An unchanged publication needs no PN/SN snapshot. Pending
			 * reorder recovery must still run below, even without new frames. */
			if (!changed || get_unaligned_le32(state + 12) == seen->frames) goto resume;
			/* PE may keep publishing; never wait for it with BH disabled.
			 * Leave both the counters and pending ARM seed for a later pass. */
			guard = get_unaligned_le32(state + 24);
			if (guard & 1) { retry = true; continue; }
			dma_rmb();
			for (unsigned int i = 0; i < 6; i++) snapshot[i] = get_unaligned_le32(state + i * 4);
			dma_rmb();
			if (guard != get_unaligned_le32(state + 24)) { retry = true; continue; }
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
				if (!rtl_ax_rx_uplink_publish(rx, sta, key, macid, tid)) retry = true;
		}
	}
	rcu_read_unlock(); local_bh_enable();
	if (!retry) rx->uplink_forwarded_seen = forwarded;
	mutex_unlock(&rx->uplink_lock);
	if (retry) {
		unsigned long flags;

		spin_lock_irqsave(&rx->irq_lock, flags);
		if (rx->running && !READ_ONCE(rx->rpq_transfer))
			/* A reorder retry must not postpone an IRQ's immediate RX work. */
			queue_delayed_work(system_wq, &rx->work, 1);
		spin_unlock_irqrestore(&rx->irq_lock, flags);
	}
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

	if (!rx->uplink_started || packet->type || packet->bytes < 24) return;
	fc = get_unaligned_le16(frame);
	if ((fc & 0x00fc) == 0x0084) {
		/* BAR can advance the BA head without accepting a new data frame. */
		tid = frame[17] >> 4;
	} else {
		if (packet->bytes < 54 || ((fc & 0x07ff) != 0x0188 && (fc & 0x07ff) != 0x0288) || frame[22] & 15) return;
		tid = frame[24] & 15;
	}
	mutex_lock(&rx->uplink_lock);
	for (macid = 1; macid < 128; macid++)
		if ((rx->keys[macid] || rx->uplink_open[macid]) && !memcmp(frame + 10, memory + 0xe400 + macid * 8, 6) &&
		    !memcmp(frame + 4, memory + RTL_AX_TX_PEER_SELF + macid * 8, 6)) break;
	if (macid == 128) goto unlock;
	rx->uplink_pending_tids[macid] |= BIT(tid);
	local_bh_disable(); rcu_read_lock();
	sta = rtl_ax_lookup_peer(rx->hw, frame + 10, frame + 4, macid);
	if (!sta || !rtl_ax_rx_uplink_publish(rx, sta, rx->keys[macid], macid, tid))
		put_unaligned_le32(0, rx->uplink_states + (macid * 16 + tid) * 32);
	rcu_read_unlock(); local_bh_enable();
unlock:
	mutex_unlock(&rx->uplink_lock);
}
