/* SPDX-License-Identifier: GPL-2.0-only */
#define RX_STATUS RTL_AX_RX_WORK_STATUS
#define RX_SLOW_RING RTL_AX_RX_ARM_RING
#define RX_PEER_CONFIG 0x10000
#define RX_ARM_DONE BIT(30)
#define RX_NI_PENDING BIT(31)
/* Transient return value only; never stored as payload ownership. */
#define RX_NI_BUSY BIT(29)

static u32 rx_buffer_dma(volatile u32 *mailbox, u32 id)
{
	const u32 *addresses = uncached(mailbox[80]);
	return addresses[id];
}

static void __attribute__((section(".entry"), noinline)) rx_idle_put(volatile u32 *mailbox, u8 *memory, u32 id)
{
	u32 count = mailbox[RTL_AX_RX_IDLE_COUNT];
	volatile u32 *tag = uncached(rx_buffer_dma(mailbox, id));
	*tag = 0;
	/* Complete the clear before a new PCIe DMA may write this tag. */
	(void)*tag;
	*(u32 *)(memory + RTL_AX_RX_IDLE_IDS + count * 4) = id;
	mailbox[RTL_AX_RX_IDLE_COUNT] = count + 1;
}

static void __attribute__((noinline)) rx_buffer_recycle(volatile u32 *mailbox, u8 *memory)
{
	u32 *held = (void *)(memory + RTL_AX_RX_NI_TAIL);
#pragma clang loop unroll(disable)
	for (u32 i = 0; i < 2; i++) {
		if (!held[i * 2]) continue;
		/* Stock NI completion retains the last two consumed descriptors. */
		if ((int)((mailbox[91] - held[i * 2 + 1] - 2) << 1) < 0) continue;
		rx_idle_put(mailbox, memory, held[i * 2] - 1);
		held[i * 2] = 0;
	}
}

static void __attribute__((noinline)) rx_work_recycle(volatile u32 *mailbox, u8 *memory)
{
	u32 read = mailbox[RTL_AX_RX_WORK_READ];
	while (read != mailbox[RTL_AX_RX_WORK_PROCESS]) {
		/* Acquire ARM completion before returning a payload to RXQ. */
		sync();
		u32 *status = (void *)(memory + RX_STATUS + read * 4);
		u32 owner = *status;
		u32 id = *(u32 *)(memory + RTL_AX_RX_WORK_IDS + read * 4) & ~RTL_AX_RX_WORK_OLD;
		if (owner & RX_NI_PENDING) {
			u32 end = owner & ~RX_NI_PENDING;
			if ((int)((mailbox[91] - end) << 1) < 0) break;
			if ((int)((mailbox[91] - end - 2) << 1) < 0) {
				u32 *held = (void *)(memory + RTL_AX_RX_NI_TAIL);
				u32 index = held[0] ? 1 : 0;
				held[index * 2] = id + 1;
				held[index * 2 + 1] = end;
			} else rx_idle_put(mailbox, memory, id);
		} else {
			if (!(owner & RX_ARM_DONE)) break;
			rx_idle_put(mailbox, memory, id);
		}
		*status = 0;
		read = (read + 1) & 255;
	}
	mailbox[RTL_AX_RX_WORK_READ] = read;
}

static u8 *__attribute__((section(".entry"), noinline)) rx_prefix(u32 *out, const u8 *data, u32 words)
{
	/* DMA payload stays uncached. Parse its small prefix in private DMEM,
	 * using aligned DDR reads even when the WLAN header is only 2-aligned. */
	const u32 *in = (const void *)((u32)data & ~3U);
	for (u32 i = 0; i < words; i++) out[i] = in[i];
	return (u8 *)out + ((u32)data & 3);
}

static u32 __attribute__((section(".rxq"), noinline, minsize)) rx_frame(
	volatile u32 *mailbox, u8 *buffer, u8 **frame, u32 *length, u32 *station, u32 *prefix)
{
	u32 info = *(u32 *)buffer, wd = *(u32 *)(buffer + 4);
	u32 bytes = wd & 0x3fff, written = info & 0x3fff;
	u32 offset = 4 + (wd & BIT(31) ? 32 : 16) + ((wd >> 14) & 3) * 2 + ((wd >> 28) & 7) * 16;
	if (wd & BIT(23)) offset += 4;
	if ((wd >> 24 & 15) || (info & 0xc000) != 0xc000 ||
	    written > 12288 || offset + bytes > written || bytes < 54) return 0;
	u32 crypto = *(u32 *)(buffer + 16);
	if (!(crypto & BIT(2)) || crypto & (BIT(1) | BIT(9) | BIT(10))) return 0;
	u8 *data = rx_prefix(prefix, buffer + offset, 10);
	u32 fc = data[0] | (u32)data[1] << 8;
	/* Protected ToDS QoS DATA; fragments use the full RX path. */
	if ((fc & 0x47ff) != 0x4188) return 0;
	if (data[22] & 15) return 0;
	u8 *memory = uncached(mailbox[187]);
	u8 *states = uncached(mailbox[106]);
	u32 macid = 128;
	/* Original RX WD5: MACID[23:16], address-CAM-valid[28]. */
	if (wd & BIT(31) && *(u32 *)(buffer + 24) & BIT(28)) macid = buffer[26];
	else for (u32 id = 1; id < 128; id++) {
		if (!(*(u32 *)(states + RX_PEER_CONFIG + id * 4) & BIT(31))) continue;
		u8 *address = memory + 0xe400 + id * 8;
		if (mac_equal(data + 10, address)) { macid = id; break; }
	}
	if (macid >= 128) return 0;
	if (!*(volatile u32 *)(memory + 0x11a00 + macid * 4)) return 0;
	u32 config = *(u32 *)(states + RX_PEER_CONFIG + macid * 4);
	if (!(config & BIT(31))) return 0;
	u8 *peer = memory + 0xe400 + macid * 8;
	u8 *ap = memory + MAILBOX_OFFSET + 64;
	if (!mac_equal(data + 4, ap) || !mac_equal(data + 10, peer)) return 0;
	u32 header = fc & 0x80 ? 26 : 24;
	if (fc & 0x8000) header += 4;
	/* mac80211 handles PS transitions and trigger-enabled U-APSD frames. */
	u32 sleeping = !!(fc & BIT(12));
	u32 old_sleeping = !!*(u32 *)(memory + 0xe800 + macid * 16);
	u32 trigger_tids = (config >> 16) & 255;
	if (sleeping != old_sleeping || (sleeping && (trigger_tids & BIT(data[24] & 15)))) {
		mailbox[121]++;
		return 0;
	}
	if (data[header + 3] != (0x20 | (config & 3) << 6)) return 0;
	if (bytes < header + 28) return 0;
	*frame = buffer + offset; *length = bytes; *station = macid;
	return header;
}

static u32 __attribute__((section(".entry"), noinline)) rx_protocol(const u8 *llc)
{
	if (((const struct packed_word *)llc)->value != 0x0003aaaa) return 0;
	u32 type = ((const struct packed_word *)(llc + 4))->value;
	if (type == 0x00080000) return llc[8] == 0x45 && (llc[17] == 6 || llc[17] == 17);
	return type == 0xdd860000 && (llc[8] >> 4) == 6 && (llc[14] == 6 || llc[14] == 17);
}

static u32 __attribute__((section(".rxq"), noinline)) rx_uplink(
	volatile u32 *mailbox, u8 *buffer, u32 buffer_dma, u32 *reorder)
{
	if (!mailbox[88] || !mailbox[93]) return 0;
	/* With FE off, Linux owns TCP/UDP too; leave the original MPDU intact. */
	volatile u32 *ni = (void *)0xb0000000;
	if ((ni[0x2300 / 4] & (BIT(31) | BIT(13))) != (BIT(31) | BIT(13)) ||
	    !(ni[0x38c4 / 4] & BIT(31))) return 0;
	u32 prefix[10], sub_prefix[9];
	u8 *wire; u32 length, macid;
	u32 header = rx_frame(mailbox, buffer, &wire, &length, &macid, prefix);
	if (!header) { mailbox[107]++; return 0; }
	u8 *frame = (u8 *)prefix + ((u32)wire & 3);
	u32 tid = frame[24] & 15, amsdu = frame[24] & 0x80;
	u32 sequence = (frame[22] | (u32)frame[23] << 8) >> 4;
	u8 *states = uncached(mailbox[106]);
	volatile u32 *state = (void *)(states + (macid * 16 + tid) * 32);
	u8 *iv = frame + header, *data = wire + header + 8;
	u32 pn_low = iv[0] | (u32)iv[1] << 8 | (u32)iv[4] << 16 | (u32)iv[5] << 24;
	u32 pn_high = iv[6] | (u32)iv[7] << 8;
	if (!(state[0] & BIT(31))) { mailbox[108]++; return 0; }
	u32 expected = state[0] & 4095, out_of_order = expected != sequence;
	if (out_of_order) {
		mailbox[109]++;
		/* A retried MPDU already behind the BA head and accepted PN is
		 * a duplicate. Recycle it without serializing fresh RX through ARM. */
		if ((frame[1] & 8) && ((sequence - expected) & 4095) >= 2048 &&
		    (pn_high < state[2] || (pn_high == state[2] && pn_low <= state[1])))
			return RX_ARM_DONE;
		if (((sequence - expected) & 4095) >= state[7]) return 0;
	}
	if (pn_high < state[2] || (pn_high == state[2] && pn_low <= state[1])) { mailbox[110]++; return 0; }
	if (out_of_order) {
		u32 order = ((macid * 16 + tid) << 12) | expected;
		u32 key = BIT(23) | (mailbox[RTL_AX_RX_WORK_PROCESS] << 24) | order;
		u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
		/* This untouched head passed the complete protocol walk before
		 * starting this scan. Retry the gap, not every A-MSDU subframe. */
		if (*(u32 *)(memory + RTL_AX_RX_REORDER_SEARCH) == key) {
			*reorder = order;
			return 0;
		}
	}
	struct { u8 addresses[12]; u32 range; } plan[63];
	/* Stock retains two completed descriptors and requires more than two
	 * free slots before each submission. The hardware read cursor alone
	 * does not release those prefetched descriptors. */
	u32 write = mailbox[90], read = mailbox[91], released = mailbox[118];
	if (read - released > 2) mailbox[118] = released = read - 2;
	u32 available = (released - write - 1) & RTL_AX_NI_TX_MASK;
	if (available <= 2) { mailbox[111]++; return RX_NI_BUSY; }
	available -= 2;
	if (available > 63) available = 63;
	u32 body = length - header - 20, offset = 0, frames = 1;
	if (!amsdu) {
		/* LLC + IPv4 + UDP is 36 bytes, producing a 42-byte NI frame. */
		if (body < 36 || body + 6 > 2048 || (frame[16] & 1) ||
		    !rx_protocol(rx_prefix(sub_prefix, data, 5))) return 0;
		header_copy(plan[0].addresses, frame + 16, 6);
		header_copy(plan[0].addresses + 6, frame + 10, 6);
		plan[0].range = (u32)(data - 6 - buffer) | (body + 6) << 16;
	} else {
		frames = 0;
		while (offset < body) {
			if (body - offset < 14) return 0;
			if (frames == available) {
				mailbox[111]++;
				return available == 63 ? 0 : RX_NI_BUSY;
			}
			u8 *sub = rx_prefix(sub_prefix, data + offset, 9);
			u32 bytes = (u32)sub[12] << 8 | sub[13];
			if (bytes < 36 || bytes + 6 > 2048 || bytes > body - offset - 14 || (sub[0] & 1) || !rx_protocol(sub + 14)) return 0;
			if (!mac_equal(sub + 6, frame + 10)) return 0;
			header_copy(plan[frames].addresses, sub, 12);
			plan[frames].range = (u32)(data + offset + 8 - buffer) | (bytes + 6) << 16;
			frames++;
			offset += bytes + 14;
			if (offset != body) offset = (offset + 3) & ~3U;
		}
		if (offset != body) return 0;
	}
	/* A protocol exception must reach ARM before later TIDs can run. */
	if (out_of_order) {
		*reorder = ((macid * 16 + tid) << 12) | expected;
		return 0;
	}
	u32 config = *(u32 *)(states + RX_PEER_CONFIG + macid * 4);
	u32 total_bytes = 0;
	/* Commit only after every subframe and the NI allowance are valid.
	 * The plan avoids a second traversal of uncached A-MSDU headers. */
	for (u32 n = 0; n < frames; n++) {
		u32 bytes = plan[n].range >> 16;
		u8 *ethernet = buffer + (plan[n].range & 65535);
		header_copy(ethernet, plan[n].addresses, 12);
		/* Stock Wi-Fi lookup maps the actual length (minimum 34), not
		 * ETH_ZLEN. Padding here would overwrite the next A-MSDU header. */
		u32 *descriptor = uncached(mailbox[89] + ((write + n) & RTL_AX_NI_TX_MASK) * 8);
		descriptor[0] = buffer_dma + (u32)(ethernet - buffer);
		descriptor[1] = 0xa5000001U | bytes << 8 | ((config >> 8) & 63) << 1;
		total_bytes += bytes;
	}
	state[6]++; sync();
	state[1] = pn_low; state[2] = pn_high;
	state[0] = BIT(31) | ((sequence + 1) & 4095);
	state[3]++; state[4] += length - 4; state[5] += frames;
	sync(); state[6]++;
	if (!(state[3] & 1023)) pe_event(mailbox);
	mailbox[96] += frames; mailbox[97] += total_bytes;
	if (amsdu) mailbox[113]++;
	/* The state release also published all packet and NI descriptor writes. */
	mailbox[90] = write + frames;
	volatile u32 *vp = (void *)mailbox[88];
	vp[0x18 / 4] = (write + frames) & RTL_AX_NI_TX_MASK;
	vp[0x68 / 4] = 1;
	return RX_NI_PENDING | ((write + frames) & ~RX_NI_PENDING);
}

static u32 __attribute__((section(".rxq"), noinline, minsize)) rx_needs_arm_barrier(
	const u8 *buffer, u32 wd)
{
	u32 type = (wd >> 24) & 15;
	if (type) return !(BIT(type) & (BIT(1) | BIT(8) | BIT(10)));
	u32 offset = 4 + (wd & BIT(31) ? 32 : 16) + ((wd >> 14) & 3) * 2 +
		((wd >> 28) & 7) * 16 + (wd & BIT(23) ? 4 : 0);
	u32 fc = buffer[offset] & 0xfc;
	/* Stock control dispatch handles BAR/PS-Poll separately. These four
	 * control subtypes do not advance receive PN/SN or peer PS state. */
	return fc != 0xb4 && fc != 0xc4 && fc != 0xd4 && fc != 0x94;
}

static inline u32 __attribute__((always_inline)) rx_reorder(
	volatile u32 *mailbox, u8 *memory, u32 read, u32 order)
{
	u32 write = mailbox[RTL_AX_RX_WORK_WRITE];
	u32 *search = (void *)(memory + RTL_AX_RX_REORDER_SEARCH);
	/* Reuse a scan only for the same capture head and BA expectation. */
	u32 key = BIT(23) | (read << 24) | order;
	volatile u32 *bar = (void *)mailbox[2];
	/* Port0 TSF runs in microseconds. Allow a short RX retry to complete
	 * before transferring the entire TID to mac80211's reorder queue. */
	if (search[0] != key) search[2] = bar[0xc438 / 4];
	u32 scan = search[0] == key ? search[1] : (read + 1) & 255;
	search[0] = key;
	sync();
	/* Resume beyond this turn's budget; the original BA window is not16. */
	for (u32 budget = 0;
	     scan != write && budget < 16; scan = (scan + 1) & 255, budget++) {
		u32 *status = (void *)(memory + RX_STATUS + scan * 4);
		u32 hint = *status;
		if ((hint & RX_NI_PENDING) || hint == RX_ARM_DONE) continue;
		/* Hints apply only while this head owns the scan. Its ARM
		 * fallback may change PS/key state before a later head runs.
		 * A candidate always repeats full admission and PN checks. */
		if (hint && (hint >> 23) == read && !((hint ^ order) & 0x7ff000) &&
		    (hint & 4095) != (order & 4095)) continue;
		u32 item = *(u32 *)(memory + RTL_AX_RX_WORK_IDS + scan * 4);
		u32 dma = rx_buffer_dma(mailbox, item & ~RTL_AX_RX_WORK_OLD);
		u8 *buffer = uncached(dma), *wire;
		u32 wd = *(u32 *)(buffer + 4);
		if (!rx_needs_arm_barrier(buffer, wd)) continue;
		u32 prefix[10], bytes, macid;
		if (!rx_frame(mailbox, buffer, &wire, &bytes, &macid, prefix)) return 0;
		u8 *frame = (u8 *)prefix + ((u32)wire & 3);
		u32 same_tid = macid == (order >> 16) &&
			(frame[24] & 15) == ((order >> 12) & 15);
		u32 sequence = (frame[22] | (u32)frame[23] << 8) >> 4;
		if (same_tid && sequence != (order & 4095)) {
			*status = read << 23 | (macid << 16) | (frame[24] & 15) << 12 | sequence;
			continue;
		}
		/* Each station/TID has independent reorder state. Commit an unrelated
		 * frame only through its complete NI/PN/SN validation. */
		u32 unused = 0, owner = rx_uplink(mailbox, buffer, dma, &unused);
		if (owner == RX_NI_BUSY) {
			/* Retry this untouched slot, not the previous head's cursor. */
			search[1] = scan;
			return 1;
		}
		if (!owner) return 0;
		*status = owner;
		mailbox[191]++;
		if (same_tid) return 1;
		mailbox[RTL_AX_RX_CROSS_TID]++;
	}
	search[1] = scan;
	if (scan != write) return 1;
	/* Capture's cached producer may have been exhausted before another
	 * DMA batch arrived. Check that batch before handing the gap to ARM. */
	if (((write + 1) & 255) != mailbox[RTL_AX_RX_WORK_READ] &&
	    write == mailbox[RTL_AX_RX_WORK_WRITE] &&
	    mailbox[81] == mailbox[RTL_AX_RX_HW_PRODUCER]) {
		mailbox[RTL_AX_RX_HW_PRODUCER] = (bar[0x1218 / 4] >> 16) & 1023;
	}
	/* Finish PE0's known capture batch before giving an SN gap to ARM.
	 * A full work ring must fall back so ARM can release its head. */
	return ((write + 1) & 255) != mailbox[RTL_AX_RX_WORK_READ] &&
		(write != mailbox[RTL_AX_RX_WORK_WRITE] ||
		 mailbox[81] != mailbox[RTL_AX_RX_HW_PRODUCER] ||
		 (u32)(bar[0xc438 / 4] - search[2]) < 1000);
}

static void __attribute__((noinline)) rx_status_poll(volatile u32 *mailbox)
{
	volatile u32 *bar = (void *)mailbox[2];
	mailbox[RTL_AX_RX_HW_PRODUCER] = (bar[0x1218 / 4] >> 16) & 1023;
	u32 rdu = bar[0x10b4 / 4] & (BIT(19) | BIT(20));
	if (rdu) {
		mailbox[RTL_AX_RX_RDU] += !!(rdu & BIT(19));
		mailbox[RTL_AX_RPQ_RDU] += !!(rdu & BIT(20));
		bar[0x10b4 / 4] = rdu;
	}
}

static void __attribute__((noinline)) rx_completion_poll(volatile u32 *mailbox)
{
	u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
	if (mailbox[88]) {
		volatile u32 *vp = (void *)mailbox[88];
		mailbox[91] += ((vp[0x1c / 4] - mailbox[91]) & RTL_AX_NI_TX_MASK);
	}
	rx_buffer_recycle(mailbox, memory);
	rx_work_recycle(mailbox, memory);
}

void __attribute__((section(".rxq"), noinline)) pe_rxq(volatile u32 *mailbox, u32 budget)
{
	if (!mailbox[80]) return;
	u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
	u32 slot = mailbox[81], write = mailbox[RTL_AX_RX_WORK_WRITE];
	u32 read = mailbox[RTL_AX_RX_WORK_READ], count = mailbox[RTL_AX_RX_IDLE_COUNT];
	u32 tag = mailbox[82], captured = 0, descriptors = mailbox[134];
	u32 *map = (void *)(memory + RTL_AX_RX_SLOT_MAP);
	u32 *work = (void *)(memory + RTL_AX_RX_WORK_IDS);
	/* PE0 alone changes these cursors. Publish once after the capture batch;
	 * processing and ARM handoff cannot consume it before this call returns. */
	for (; captured < budget; captured++) {
		if (slot == mailbox[RTL_AX_RX_HW_PRODUCER]) rx_status_poll(mailbox);
		u32 next = (write + 1) & 255;
		if (next == read || mailbox[RTL_AX_RX_HW_PRODUCER] == slot) break;
		u32 id = map[slot];
		u32 info = *(volatile u32 *)uncached(rx_buffer_dma(mailbox, id));
		u32 next_tag = tag == 8191 ? 1 : tag + 1;
		if (((info >> 16) & 8191) != next_tag) {
			/* RDU remains observable even inside a cached producer batch. */
			rx_status_poll(mailbox);
			break;
		}
		sync();
		work[write] = id | (rtl_ax_rx_epoch_old(&mailbox[RTL_AX_PE_DFS_EPOCH], slot) ? RTL_AX_RX_WORK_OLD : 0);
		u32 replacement = *(u32 *)(memory + RTL_AX_RX_IDLE_IDS + --count * 4);
		map[slot] = replacement;
		volatile u32 *descriptor = uncached(descriptors + slot * 8);
		descriptor[1] = rx_buffer_dma(mailbox, replacement);
		/* Drain the DDR address update before returning this RXBD to PCIe. */
		(void)descriptor[1];
		write = next;
		tag = next_tag;
		slot = (slot + 1) & 255;
	}
	if (!captured) return;
	sync();
	mailbox[RTL_AX_RX_IDLE_COUNT] = count;
	mailbox[RTL_AX_RX_WORK_WRITE] = write;
	mailbox[82] = tag;
	mailbox[86] += captured;
	mailbox[81] = slot;
}

static void __attribute__((noinline)) rx_notify_done(volatile u32 *mailbox)
{
	if (mailbox[83] == mailbox[81]) return;
	/* Stock _phl_refill_rxbd updates the batch before notify_rxdone.
	 * Publish on every exit, including a short/unready/full work batch. */
	sync();
	*(volatile u16 *)(mailbox[2] + 0x1218) = mailbox[81];
	mailbox[83] = mailbox[81];
}

static void __attribute__((section(".rxq"), noinline)) pe_rxq_batch(volatile u32 *mailbox)
{
	/* Capture does not process work. One completion snapshot supplies the
	 * entire batch; newer NI/ARM releases are collected on the next turn. */
	rx_completion_poll(mailbox);
	pe_rxq(mailbox, 16);
	rx_notify_done(mailbox);
}

static void __attribute__((section(".rxq"), noinline, minsize)) pe_rx_work(volatile u32 *mailbox)
{
	u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
	/* Only receive-state handlers serialize subsequent WLAN data. Factory
	 * paths separate reports; native PHY/DFS/C2H and ordinary control
	 * handlers do not update receive PN/SN or PS. */
	if (mailbox[94] == mailbox[95]) mailbox[RTL_AX_RX_ARM_BARRIER] = 0;
	if (mailbox[RTL_AX_RX_ARM_BARRIER]) return;
	u32 read = mailbox[RTL_AX_RX_WORK_PROCESS];
	if (read == mailbox[RTL_AX_RX_WORK_WRITE]) return;
	/* Capture publishes the payload ID before the write cursor. */
	sync();
	/* Lookahead may forward a frame or finish discarding an old retry. */
	u32 completed = *(u32 *)(memory + RX_STATUS + read * 4);
	if ((completed & RX_NI_PENDING) || completed == RX_ARM_DONE) goto advance;
	u32 item = *(u32 *)(memory + RTL_AX_RX_WORK_IDS + read * 4), id = item & ~RTL_AX_RX_WORK_OLD;
	u32 buffer_dma = rx_buffer_dma(mailbox, id);
	u8 *buffer = uncached(buffer_dma);
	u32 wd = *(u32 *)(buffer + 4), type = (wd >> 24) & 15;
	u32 reorder = 0, owner = (item & RTL_AX_RX_WORK_OLD) && type == 8 ?
		RX_ARM_DONE : rx_uplink(mailbox, buffer, buffer_dma, &reorder);
	/* NI congestion retains the untouched head until the next completion
	 * poll, rather than feeding the same congested path through ARM. */
	if (owner == RX_NI_BUSY) return;
	if (reorder && rx_reorder(mailbox, memory, read, reorder)) return;
	*(u32 *)(memory + RX_STATUS + read * 4) = owner;
	if (!owner) {
		u32 producer = mailbox[94];
		if (((producer + 1) & 255) == mailbox[95]) return;
		u32 stateful = rx_needs_arm_barrier(buffer, wd);
		*(u32 *)(memory + RX_SLOW_RING + producer * 4) = read;
		sync();
		mailbox[94] = (producer + 1) & 255;
		if (stateful) mailbox[RTL_AX_RX_ARM_BARRIER] = 1;
		if (producer == mailbox[95]) pe_event(mailbox);
	}
	/* The next capture turn may recycle payloads behind this cursor. */
advance:
	*(u32 *)(memory + RTL_AX_RX_REORDER_SEARCH) = 0;
	sync();
	mailbox[RTL_AX_RX_WORK_PROCESS] = (read + 1) & 255;
}

static void __attribute__((section(".rxq"), noinline)) pe_rx_work_batch(volatile u32 *mailbox)
{
	/* A queued packet checks the ARM acknowledgement before use. */
	if (mailbox[RTL_AX_RX_WORK_PROCESS] == mailbox[RTL_AX_RX_WORK_WRITE]) return;
#pragma clang loop unroll(disable)
	for (u32 budget = 0; budget < 16; budget++) {
		u32 before = mailbox[RTL_AX_RX_WORK_PROCESS];
		pe_rx_work(mailbox);
		if (before == mailbox[RTL_AX_RX_WORK_PROCESS]) break;
	}
}
