/* SPDX-License-Identifier: GPL-2.0-only */
#define RX_STATUS 0xf000
#define RX_SLOW_RING 0xf400
#define RX_PEER_CONFIG 0x10000
#define RX_ARM_DONE BIT(30)
#define RX_NI_PENDING BIT(31)
#define RX_BUFFER_MAP 0x17400
#define RX_BUFFER_FREE 0x17800
#define RX_BUFFER_HELD 0x17810

static u32 rx_buffer(u8 *memory, u32 slot)
{
	return *(u32 *)(memory + RX_BUFFER_MAP + slot * 4);
}

static void __attribute__((noinline)) rx_buffer_recycle(volatile u32 *mailbox, u8 *memory)
{
	u32 *free = (void *)(memory + RX_BUFFER_FREE);
	u32 *held = (void *)(memory + RX_BUFFER_HELD);
	for (u32 i = 0; i < 2; i++) {
		if (!held[i * 2]) continue;
		/* Stock NI completion retains the last two consumed descriptors. */
		if ((int)((mailbox[91] - held[i * 2 + 1] - 2) << 1) < 0) continue;
		u32 id = held[i * 2] - 1;
		*(volatile u32 *)uncached(mailbox[80] + id * 12288) = 0;
		free[free[0] ? 1 : 0] = id + 1;
		held[i * 2] = 0;
	}
}

static u32 __attribute__((noinline)) rx_buffer_return(volatile u32 *mailbox, u8 *memory, u32 slot, u32 end)
{
	u32 id = rx_buffer(memory, slot);
	if ((int)((mailbox[91] - end - 2) << 1) >= 0) {
		*(volatile u32 *)uncached(mailbox[80] + id * 12288) = 0;
		return 1;
	}
	u32 *free = (void *)(memory + RX_BUFFER_FREE);
	u32 *held = (void *)(memory + RX_BUFFER_HELD);
	u32 available = free[0] ? 0 : 1;
	if (!free[available]) return 0;
	u32 index = held[0] ? 1 : 0, replacement = free[available] - 1;
	held[index * 2] = id + 1;
	held[index * 2 + 1] = end;
	free[available] = 0;
	*(u32 *)(memory + RX_BUFFER_MAP + slot * 4) = replacement;
	volatile u32 *descriptor = uncached(mailbox[134] + slot * 8);
	descriptor[1] = mailbox[80] + replacement * 12288;
	return 1;
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
	if (mailbox[94] != mailbox[95]) { mailbox[120]++; return 0; }
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
	volatile u32 *status = (void *)(memory + RX_STATUS);
	if (mailbox[88]) {
		volatile u32 *vp = (void *)mailbox[88];
		mailbox[91] += ((vp[0x1c / 4] & 63) - (mailbox[91] & 63)) & 63;
	}
	rx_buffer_recycle(mailbox, memory);
	volatile u32 *bar = (void *)mailbox[2];
	u32 returned = mailbox[83], scan = mailbox[81];
	while (returned != scan) {
		u32 owner = status[returned];
		/* NI carries a 31-bit sequence: its bit30 is not an ARM release. */
		if (owner & RX_NI_PENDING) {
			if ((int)((mailbox[91] - (owner & ~RX_NI_PENDING)) << 1) < 0) break;
			if (!rx_buffer_return(mailbox, memory, returned, owner & ~RX_NI_PENDING)) break;
		} else {
			if (!(owner & RX_ARM_DONE)) break;
			*(volatile u32 *)uncached(mailbox[80] + rx_buffer(memory, returned) * 12288) = 0;
		}
		status[returned] = 0;
		returned = (returned + 1) & 255;
	}
	if (returned != mailbox[83]) {
		mailbox[83] = returned;
		sync();
		*(volatile u16 *)((u8 *)bar + 0x1218) = returned;
	}
	/* ARM must publish exception PN/SN/PS state before the next RX frame.
	 * Wait here instead of sending the rest of that burst through ARM. */
	if (mailbox[94] != mailbox[95]) return;
	if (((bar[0x1218 / 4] >> 16) & 1023) == scan) return;
	u32 buffer_dma = mailbox[80] + rx_buffer(memory, scan) * 12288;
	u8 *buffer = uncached(buffer_dma);
	u32 info = *(volatile u32 *)buffer;
	u32 tag = mailbox[82] == 8191 ? 1 : mailbox[82] + 1;
	if (((info >> 16) & 8191) != tag) return;
	sync();
	u32 owner = rx_uplink(mailbox, buffer, buffer_dma);
	if (owner) status[scan] = owner;
	else {
		u32 producer = mailbox[94];
		if (((producer + 1) & 255) == mailbox[95]) return;
		status[scan] = 0;
		*(u32 *)(memory + RX_SLOW_RING + producer * 4) = scan;
		sync();
		mailbox[94] = (producer + 1) & 255;
		if (producer == mailbox[95]) pe_event(mailbox);
	}
	mailbox[82] = tag;
	mailbox[86]++;
	sync();
	mailbox[81] = (scan + 1) & 255;
}
