/* SPDX-License-Identifier: GPL-2.0-only */
#define NI_WAIT 0x11000
#define NI_PEER_QUEUED 0x11800
#define NI_PEER_ROUTE 0x11a00
#define NI_PEER_BA 0x12000
#define NI_TID_QUEUED 0x13000
#define NI_TIM_MASK 0x14000
#define NI_TID_PENDING 0x14200
#define NI_TID_HEAD 0x15000
#define NI_TID_TAIL 0x16000
#define NI_EPP_CONSUMERS 0x11c00

static void __attribute__((noinline)) ni_peer_event(volatile u32 *mailbox, u8 *memory, u32 macid)
{
	u32 mask = 0;
	if (*(u32 *)(memory + 0xe800 + macid * 16))
		for (u32 tid = 0; tid < 8; tid++)
			if (memory[NI_TID_PENDING + macid * 8 + tid]) mask |= BIT(tid);
	u32 *old = (void *)(memory + NI_TIM_MASK + macid * 4);
	if (*old != mask) { *old = mask; pe_event(mailbox); }
}

static u32 __attribute__((section(".rxq"), noinline)) ni_ingest(volatile u32 *mailbox, u8 *memory)
{
	volatile u32 *ni = (void *)0xb0000000;
	volatile u32 *owners = (void *)(memory + 0xc400);
	if (mailbox[30] >= 124 || mailbox[47]) return 0;
	/* Stock NIC selects the highest COS from EPP_STATUS0, then checks WTP. */
	u32 available = (ni[0x659c / 4] >> ((mailbox[114] - 0x7000) / 4)) & 255;
	if (!available) return 0;
	u32 cos = 0;
	if (available & 0xf0) { available >>= 4; cos = 4; }
	if (available & 0x0c) { available >>= 2; cos += 2; }
	if (available & 0x02) cos++;
	volatile u32 *consumers = (void *)(memory + NI_EPP_CONSUMERS);
	u32 consumer = consumers[cos];
	u32 ring_bytes = mailbox[167];
	if ((ni[mailbox[114] / 4 + cos] & (ring_bytes - 1)) == consumer) return 0;
	sync();
	const u32 *entry = uncached(mailbox[25] + cos * ring_bytes + consumer);
	if ((*(volatile u32 *)entry & 0x50) != 0x50) return 0;
	u32 buffer = entry[0] & 0xffffff80, header, length = ni_length(entry, buffer, &header);
	u8 *ethernet = uncached(buffer + 32 + header);
	u32 id = 0, macid = 128, free_slot;
	/* Allocation advances independently of the next queue to transmit. */
	for (free_slot = 0; free_slot < 128; free_slot++) {
		id = (mailbox[166] + free_slot) & 127;
		if (!owners[id * 2]) break;
	}
	if (free_slot == 128) return 0;
	mailbox[166] = (id + 1) & 127;
	if ((entry[0] & 0x50) == 0x50 && length >= 14 && length <= 2048 - 32 - header)
		for (u32 peer = 1; peer < 128; peer++) {
			if (!*(u32 *)(memory + NI_PEER_ROUTE + peer * 4)) continue;
			u8 *address = memory + 0xe400 + peer * 8;
			u32 i;
			for (i = 0; i < 6 && address[i] == ethernet[i]; i++);
			if (i == 6) { macid = peer; break; }
		}
	owners[id * 2] = buffer;
	mailbox[30]++;
	if (macid == 128 || (mailbox[62] == 128 || mailbox[62] == macid)) {
		mailbox[128] = entry[0]; mailbox[129] = entry[1];
		mailbox[130] = length; mailbox[131] = header;
		mailbox[132] = big_word(ethernet); mailbox[133] = big_word(ethernet + 4);
		owners[id * 2 + 1] = BIT(31);
		mailbox[47] = id + 1;
		if (macid == 128) mailbox[40]++; else mailbox[63]++;
	} else {
		u32 key = *(u32 *)(memory + 0xd000 + macid * 32 + 20);
		u32 *waiting = (void *)(memory + NI_WAIT + id * 16);
		waiting[0] = buffer + 32 + header;
		waiting[1] = length;
		waiting[2] = macid;
		/* Native NI HEADER_A carries COS in the low three bits. */
		waiting[3] = big_word(uncached(buffer + 36)) & 7;
		u32 queue = macid * 8 + waiting[3];
		u32 *tail = (void *)(memory + NI_TID_TAIL + queue * 4);
		if (*tail) (*(u32 *)(memory + NI_WAIT + (*tail - 1) * 16 + 12)) |= (id + 1) << 8;
		else *(u32 *)(memory + NI_TID_HEAD + queue * 4) = id + 1;
		*tail = id + 1;
		owners[id * 2 + 1] = macid << 8 | ((key >> 16) & 127) << 17 | BIT(15);
		(*(u32 *)(memory + NI_PEER_QUEUED + macid * 4))++;
		mailbox[38]++;
		(*(u32 *)(memory + NI_TID_QUEUED + (macid * 8 + waiting[3]) * 4))++;
		memory[NI_TID_PENDING + macid * 8 + waiting[3]]++;
		ni_peer_event(mailbox, memory, macid);
	}
	consumer = (consumer + 8) & (ring_bytes - 1);
	consumers[cos] = consumer;
	mailbox[26] = consumers[0];
	sync(); ni[mailbox[115] / 4 + cos] = consumer;
	return 1;
}

static u32 __attribute__((section(".rxq"), noinline)) ni_select(volatile u32 *mailbox, u8 *memory, u32 *request)
{
	if (!mailbox[38]) return 0;
	volatile u32 *owners = (void *)(memory + 0xc400);
	static const u8 channels[8] = {0, 1, 1, 0, 2, 2, 3, 3};
	static const u8 indicators[8] = {0, 0, 1, 1, 0, 1, 0, 1};
	for (u32 offset = 0; offset < 128; offset++) {
		u32 id = (mailbox[112] + offset) & 127;
		u32 *waiting = (void *)(memory + NI_WAIT + id * 16);
		if (!waiting[0]) continue;
		u32 macid = waiting[2], tid = waiting[3] & 7;
		u32 *head = (void *)(memory + NI_TID_HEAD + (macid * 8 + tid) * 4);
		if (*head != id + 1) continue;
		volatile u32 *ps = (void *)(memory + 0xe800 + macid * 16);
		u32 queued = *(u32 *)(memory + NI_PEER_QUEUED + macid * 4);
		u32 retired = (mailbox[62] == 128 || mailbox[62] == macid) ||
			!*(volatile u32 *)(memory + NI_PEER_ROUTE + macid * 4);
		u32 frames = 1;
		if (retired) {
			if (mailbox[47]) continue;
			owners[id * 2 + 1] |= BIT(31);
			mailbox[47] = id + 1;
			mailbox[63]++;
		} else {
			if (ps[0] && (ps[1] == ps[2] || !((ps[3] >> 16) & BIT(tid)))) continue;
			const u32 *peer = (void *)(memory + 0xd000 + macid * 32);
			for (u32 i = 0; i < 8; i++) request[i] = peer[i];
			request[0] = 256 + id; request[1] = channels[tid];
			request[2] = BIT(24) | (peer[2] & 0x3f0000) | (waiting[1] + ((peer[2] >> 16) & 63) + 8 - 14);
			request[4] = (peer[4] & ~0x1fffU) | tid | indicators[tid] << 12;
			request[5] = (peer[5] & ~255U) | *(u32 *)(memory + NI_PEER_BA + (macid * 8 + tid) * 4);
			request[8] = waiting[0]; request[9] = waiting[1];
			u32 caps = *(u32 *)(memory + 0xe200 + macid * 4);
			if (caps & BIT(17)) request[3] |= BIT(20);
			if (ps[0]) {
				request[4] |= (queued > 1 || (ps[3] & 2) ? BIT(19) : 0) | (ps[3] & 1 ? BIT(20) : 0);
				owners[id * 2 + 1] |= BIT(16);
				ps[2] = ps[1]; mailbox[39]++;
			} else {
				ps[2] = ps[1];
				u32 next = waiting[3] >> 8, tail_id = id;
				u32 body = waiting[1] + 8;
				if (((request[2] >> 16) & 63) == 26 && !(request[3] & BIT(19))) {
					while (next && frames < 8) {
						u32 other = next - 1;
						u32 *part = (void *)(memory + NI_WAIT + other * 16);
						u32 pad = (0U - body) & 3;
						if (body + pad + part[1] + 8 > (caps >> 18)) break;
						request[8 + frames * 2] = part[0];
						request[9 + frames * 2] = part[1];
						body += pad + part[1] + 8;
						owners[tail_id * 2 + 1] = (owners[tail_id * 2 + 1] & ~BIT(15)) | other << 24;
						tail_id = other;
						next = part[3] >> 8;
						part[0] = 0;
						frames++;
					}
				}
				if (frames > 1) {
					request[4] |= BIT(21);
					request[2] = frames << 24 | 26U << 16 | (26 + body);
					waiting[3] = (waiting[3] & 7) | next << 8;
				}
			}
		}
		*head = waiting[3] >> 8;
		if (!*head) *(u32 *)(memory + NI_TID_TAIL + (macid * 8 + tid) * 4) = 0;
		waiting[0] = 0;
		(*(u32 *)(memory + NI_PEER_QUEUED + macid * 4)) -= frames;
		mailbox[38] -= frames;
		(*(u32 *)(memory + NI_TID_QUEUED + (macid * 8 + tid) * 4)) -= frames;
		mailbox[112] = (id + 1) & 127;
		ni_peer_event(mailbox, memory, macid);
		if (!retired) {
			(*(u32 *)(memory + 0x17000 + macid * 4)) += frames;
			(*(u32 *)(memory + 0x17200 + macid * 4)) |= BIT(tid);
			return frames;
		}
	}
	return 0;
}
