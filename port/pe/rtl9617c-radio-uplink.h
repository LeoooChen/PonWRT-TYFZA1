/* SPDX-License-Identifier: GPL-2.0-only */
#define RX_STATUS RTL_AX_RX_WORK_STATUS
#define RX_SLOW_RING RTL_AX_RX_ARM_RING
#define RX_PEER_CONFIG 0x10000
#define RX_ARM_DONE BIT(30)
#define RX_NI_PENDING BIT(31)

static u32 rx_buffer_dma(volatile u32 *mailbox, u32 id)
{
	const u32 *addresses = uncached(mailbox[80]);
	return addresses[id];
}

static void rx_idle_put(volatile u32 *mailbox, u8 *memory, u32 id)
{
	*(volatile u32 *)uncached(rx_buffer_dma(mailbox, id)) = 0;
	*(u32 *)(memory + RTL_AX_RX_IDLE_IDS + mailbox[RTL_AX_RX_IDLE_COUNT] * 4) = id;
	mailbox[RTL_AX_RX_IDLE_COUNT]++;
}

static void __attribute__((noinline)) rx_buffer_recycle(volatile u32 *mailbox, u8 *memory)
{
	u32 *held = (void *)(memory + RTL_AX_RX_NI_TAIL);
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

static u32 rx_frame(volatile u32 *mailbox, u8 *buffer, u8 **frame, u32 *length, u32 *station)
{
	u32 info = *(u32 *)buffer, wd = *(u32 *)(buffer + 4);
	u32 bytes = wd & 0x3fff, written = info & 0x3fff;
	u32 offset = 4 + (wd & BIT(31) ? 32 : 16) + ((wd >> 14) & 3) * 2 + ((wd >> 28) & 7) * 16;
	if (wd & BIT(23)) offset += 4;
	if ((wd >> 24 & 15) || (info & 0xc000) != 0xc000 ||
	    written > 12288 || offset + bytes > written || bytes < 54) return 0;
	u32 crypto = *(u32 *)(buffer + 16);
	if (!(crypto & BIT(2)) || crypto & (BIT(1) | BIT(9) | BIT(10))) return 0;
	u8 *data = buffer + offset;
	u32 fc = data[0] | (u32)data[1] << 8;
	/* Protected ToDS QoS DATA; fragments use the full RX path. */
	if ((fc & 0x47ff) != 0x4188) return 0;
	if (data[22] & 15) return 0;
	u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
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
	u8 *ap = (void *)(mailbox + 16);
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
	*frame = data; *length = bytes; *station = macid;
	return header;
}

static u32 __attribute__((section(".rxq"), noinline)) rx_protocol(const u8 *llc)
{
	if (llc[0] != 0xaa || llc[1] != 0xaa || llc[2] != 3 || llc[3] || llc[4] || llc[5]) return 0;
	if (llc[6] == 8 && !llc[7]) return llc[8] == 0x45 && (llc[17] == 6 || llc[17] == 17);
	return llc[6] == 0x86 && llc[7] == 0xdd && (llc[8] >> 4) == 6 && (llc[14] == 6 || llc[14] == 17);
}

static u32 __attribute__((section(".rxq"), noinline)) rx_uplink(volatile u32 *mailbox, u8 *buffer, u32 buffer_dma)
{
	if (!mailbox[88] || !mailbox[93]) return 0;
	/* With FE off, Linux owns TCP/UDP too; leave the original MPDU intact. */
	volatile u32 *ni = (void *)0xb0000000;
	if ((ni[0x2300 / 4] & (BIT(31) | BIT(13))) != (BIT(31) | BIT(13)) ||
	    !(ni[0x38c4 / 4] & BIT(31))) return 0;
	u8 *frame; u32 length, macid;
	u32 header = rx_frame(mailbox, buffer, &frame, &length, &macid);
	if (!header) { mailbox[107]++; return 0; }
	u32 tid = frame[24] & 15, amsdu = frame[24] & 0x80;
	u32 sequence = (frame[22] | (u32)frame[23] << 8) >> 4;
	u8 *states = uncached(mailbox[106]);
	volatile u32 *state = (void *)(states + (macid * 16 + tid) * 32);
	u8 *iv = frame + header, *data = iv + 8;
	u32 pn_low = iv[0] | (u32)iv[1] << 8 | (u32)iv[4] << 16 | (u32)iv[5] << 24;
	u32 pn_high = iv[6] | (u32)iv[7] << 8;
	if (!(state[0] & BIT(31))) { mailbox[108]++; return 0; }
	if ((state[0] & 4095) != sequence) { mailbox[109]++; return 0; }
	if (pn_high < state[2] || (pn_high == state[2] && pn_low <= state[1])) { mailbox[110]++; return 0; }
	u32 body = length - header - 20, offset = 0, frames = 1;
	if (!amsdu) {
		/* LLC + IPv4 + UDP is 36 bytes; standalone RX buffers have room
		 * for the Ethernet minimum-frame padding applied below. */
		if (body < 36 || !rx_protocol(data) || (frame[16] & 1) || body + 6 > 2048) return 0;
	} else {
		frames = 0;
		while (offset < body) {
			if (body - offset < 14) return 0;
			u8 *sub = data + offset;
			u32 bytes = (u32)sub[12] << 8 | sub[13];
			if (bytes < 54 || bytes + 6 > 2048 || bytes > body - offset - 14 || (sub[0] & 1) || !rx_protocol(sub + 14)) return 0;
			if (!mac_equal(sub + 6, frame + 10)) return 0;
			frames++;
			offset += bytes + 14;
			if (offset != body) offset = (offset + 3) & ~3U;
		}
		if (offset != body) return 0;
	}
	u32 write = mailbox[90];
	if (frames > 63 - (write - mailbox[91])) { mailbox[111]++; return 0; }
	u32 config = *(u32 *)(states + RX_PEER_CONFIG + macid * 4);
	u32 total_bytes = 0;
	offset = 0;
	for (u32 n = 0; n < frames; n++) {
		u8 addresses[12], *ethernet;
		u32 bytes;
		if (amsdu) {
			u8 *sub = data + offset;
			u32 payload = (u32)sub[12] << 8 | sub[13];
			header_copy(addresses, sub, 12);
			ethernet = sub + 8; bytes = payload + 6;
			offset = (offset + payload + 17) & ~3U;
		} else {
			header_copy(addresses, frame + 16, 6);
			header_copy(addresses + 6, frame + 10, 6);
			ethernet = data - 6; bytes = body + 6;
		}
		header_copy(ethernet, addresses, 12);
		if (bytes < 60) { for (u32 i = bytes; i < 60; i++) ethernet[i] = 0; bytes = 60; }
		u32 *descriptor = uncached(mailbox[89] + ((write + n) & 63) * 8);
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
	sync();
	mailbox[90] = write + frames;
	volatile u32 *vp = (void *)mailbox[88];
	vp[0x18 / 4] = (write + frames) & 63;
	vp[0x68 / 4] = 1;
	return RX_NI_PENDING | ((write + frames) & ~RX_NI_PENDING);
}

static void __attribute__((section(".rxq"), noinline)) pe_rxq(volatile u32 *mailbox)
{
	if (!mailbox[80]) return;
	u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
	volatile u32 *bar = (void *)mailbox[2];
	u32 rdu = bar[0x10b4 / 4] & (BIT(19) | BIT(20));
	if (rdu) {
		mailbox[RTL_AX_RX_RDU] += !!(rdu & BIT(19));
		mailbox[RTL_AX_RPQ_RDU] += !!(rdu & BIT(20));
		bar[0x10b4 / 4] = rdu;
	}
	if (mailbox[88]) {
		volatile u32 *vp = (void *)mailbox[88];
		mailbox[91] += ((vp[0x1c / 4] & 63) - (mailbox[91] & 63)) & 63;
	}
	rx_buffer_recycle(mailbox, memory);
	rx_work_recycle(mailbox, memory);
	u32 *work = (void *)(memory + RTL_AX_RX_WORK_IDS);
	u32 slot = mailbox[81], write = mailbox[RTL_AX_RX_WORK_WRITE];
	u32 next = (write + 1) & 255;
	if (next != mailbox[RTL_AX_RX_WORK_READ] &&
	    ((bar[0x1218 / 4] >> 16) & 1023) != slot) {
		u32 *map = (void *)(memory + RTL_AX_RX_SLOT_MAP);
		u32 id = map[slot];
		u32 info = *(volatile u32 *)uncached(rx_buffer_dma(mailbox, id));
		u32 tag = mailbox[82] == 8191 ? 1 : mailbox[82] + 1;
		if (((info >> 16) & 8191) == tag) {
			sync();
			work[write] = id | (rtl_ax_rx_epoch_old(&mailbox[RTL_AX_PE_DFS_EPOCH], slot) ? RTL_AX_RX_WORK_OLD : 0);
			u32 count = mailbox[RTL_AX_RX_IDLE_COUNT] - 1;
			u32 replacement = *(u32 *)(memory + RTL_AX_RX_IDLE_IDS + count * 4);
			mailbox[RTL_AX_RX_IDLE_COUNT] = count;
			map[slot] = replacement;
			volatile u32 *descriptor = uncached(mailbox[134] + slot * 8);
			descriptor[1] = rx_buffer_dma(mailbox, replacement);
			sync();
			mailbox[RTL_AX_RX_WORK_WRITE] = next;
			mailbox[82] = tag;
			mailbox[86]++;
			mailbox[81] = mailbox[83] = (slot + 1) & 255;
			sync();
			*(volatile u16 *)((u8 *)bar + 0x1218) = mailbox[81];
		}
	}
	/* Only receive-state handlers serialize subsequent WLAN data. Factory
	 * paths separate reports; native PHY/DFS/C2H and ordinary control
	 * handlers do not update receive PN/SN or PS. */
	if (mailbox[94] == mailbox[95]) mailbox[RTL_AX_RX_ARM_BARRIER] = 0;
	if (mailbox[RTL_AX_RX_ARM_BARRIER]) return;
	u32 read = mailbox[RTL_AX_RX_WORK_PROCESS];
	if (read == mailbox[RTL_AX_RX_WORK_WRITE]) return;
	u32 item = work[read], id = item & ~RTL_AX_RX_WORK_OLD;
	u32 buffer_dma = rx_buffer_dma(mailbox, id);
	u8 *buffer = uncached(buffer_dma);
	u32 wd = *(u32 *)(buffer + 4), type = (wd >> 24) & 15;
	u32 stateful = type != 1 && type != 8 && type != 10;
	if (!type) {
		u32 offset = 4 + (wd & BIT(31) ? 32 : 16) + ((wd >> 14) & 3) * 2 +
			((wd >> 28) & 7) * 16 + (wd & BIT(23) ? 4 : 0);
		u32 fc = buffer[offset] & 0xfc;
		if (fc == 0xb4 || fc == 0xc4 || fc == 0xd4 || fc == 0x94) stateful = 0;
	}
	u32 owner = (item & RTL_AX_RX_WORK_OLD) && type == 8 ?
		RX_ARM_DONE : rx_uplink(mailbox, buffer, buffer_dma);
	*(u32 *)(memory + RX_STATUS + read * 4) = owner;
	if (!owner) {
		u32 producer = mailbox[94];
		if (((producer + 1) & 255) == mailbox[95]) return;
		*(u32 *)(memory + RX_SLOW_RING + producer * 4) = read;
		sync();
		mailbox[94] = (producer + 1) & 255;
		if (stateful) mailbox[RTL_AX_RX_ARM_BARRIER] = 1;
		if (producer == mailbox[95]) pe_event(mailbox);
	}
	sync();
	mailbox[RTL_AX_RX_WORK_PROCESS] = (read + 1) & 255;
}
