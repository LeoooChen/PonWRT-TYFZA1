/* SPDX-License-Identifier: GPL-2.0-only */
#define NI_WAIT 0x11000
#define NI_PEER_QUEUED 0x11800
#define NI_PEER_ROUTE 0x11a00
#define NI_PEER_BA 0x12000
#define NI_TID_QUEUED 0x13000
#define NI_TIM_MASK 0x14000
#define NI_TID_PENDING 0x18000
#define NI_PEER_NEXT 0x17400
#define NI_PEER_PREV 0x17600
#define NI_TID_HEAD 0x15000
#define NI_TID_TAIL 0x16000
#define NI_EPP_CONSUMERS 0x11c00

static void __attribute__((noinline)) ni_peer_event(volatile u32 *mailbox, u8 *memory, u32 macid)
{
	u32 mask = 0;
	if (*(u32 *)(memory + 0xe800 + macid * 16))
		for (u32 tid = 0; tid < 8; tid++)
			if (*(u32 *)(memory + NI_TID_PENDING + (macid * 8 + tid) * 4)) mask |= BIT(tid);
	u32 *old = (void *)(memory + NI_TIM_MASK + macid * 4);
	if (*old != mask) { *old = mask; pe_event(mailbox); }
}

/* Queue metadata uses the 32-byte NI BID headroom, before HEADER_A.
 * Hardware WP ownership starts only when a queued frame is selected. */
static void ni_peer_add(volatile u32 *mailbox, u8 *memory, u32 macid)
{
	u32 *next = (void *)(memory + NI_PEER_NEXT), *prev = (void *)(memory + NI_PEER_PREV);
	u32 head = mailbox[170], node = macid + 1;
	if (!head) { mailbox[170] = node; next[macid] = prev[macid] = node; }
	else {
		u32 tail = prev[head - 1];
		next[tail - 1] = node; prev[macid] = tail;
		next[macid] = head; prev[head - 1] = node;
	}
}

static u32 __attribute__((section(".rxq"), noinline)) ni_ingest(volatile u32 *mailbox, u8 *memory)
{
	volatile u32 *ni = (void *)0xb0000000;
	u32 available = (ni[0x659c / 4] >> ((mailbox[114] - 0x7000) / 4)) & 255;
	if (!available) return 0;
	u32 cos = 0;
	if (available & 0xf0) { available >>= 4; cos = 4; }
	if (available & 0x0c) { available >>= 2; cos += 2; }
	if (available & 0x02) cos++;
	volatile u32 *consumers = (void *)(memory + NI_EPP_CONSUMERS);
	u32 consumer = consumers[cos];
	u32 ring_shift = mailbox[167], ring_bytes = 1U << ring_shift;
	if ((ni[mailbox[114] / 4 + cos] & (ring_bytes - 1)) == consumer) return 0;
	sync();
	const u32 *entry = uncached(mailbox[25] + (cos << ring_shift) + consumer);
	if ((*(volatile u32 *)entry & 0x50) != 0x50) return 0;
	u32 buffer = entry[0] & 0xffffff80, header, length = ni_length(entry, buffer, &header);
	u8 *ethernet = uncached(buffer + 32 + header);
	u32 macid = 0;
	if (length >= 14 && length <= 2048 - 32 - header)
		for (u32 peer = 1; peer < 128; peer++) {
			if (!*(u32 *)(memory + NI_PEER_ROUTE + peer * 4)) continue;
			if (mac_equal(memory + 0xe400 + peer * 8, ethernet)) { macid = peer; break; }
		}
	if (!macid) {
		mailbox[128] = entry[0]; mailbox[129] = entry[1];
		mailbox[130] = length; mailbox[131] = header;
		mailbox[132] = big_word(ethernet); mailbox[133] = big_word(ethernet + 4);
		mailbox[40]++;
	}
	u32 tid = macid ? big_word(uncached(buffer + 36)) & 7 : 0;
	u32 *waiting = uncached(buffer);
	waiting[0] = buffer + 32 + header; waiting[1] = length;
	waiting[2] = macid; waiting[3] = tid;
	u32 queue = macid * 8 + tid;
	u32 *tail = (void *)(memory + NI_TID_TAIL + queue * 4);
	if (*tail) (*(u32 *)uncached(*tail + 12)) |= buffer;
	else *(u32 *)(memory + NI_TID_HEAD + queue * 4) = buffer;
	*tail = buffer;
	u32 *queued = (void *)(memory + NI_PEER_QUEUED + macid * 4);
	if (!(*queued)++) ni_peer_add(mailbox, memory, macid);
	mailbox[38]++;
	(*(u32 *)(memory + NI_TID_QUEUED + queue * 4))++;
	if (macid) (*(u32 *)(memory + NI_TID_PENDING + queue * 4))++;
	ni_peer_event(mailbox, memory, macid);
	consumer = (consumer + 8) & (ring_bytes - 1);
	consumers[cos] = consumer; mailbox[26] = consumers[0];
	sync(); ni[mailbox[115] / 4 + cos] = consumer;
	return 1;
}

static u32 ni_owner(volatile u32 *mailbox, u8 *memory)
{
	volatile u32 *owners = (void *)(memory + 0xc400);
	u32 id = mailbox[166];
	while (owners[id * 2]) id = (id + 1) & 127;
	mailbox[166] = (id + 1) & 127;
	return id;
}

static u32 __attribute__((section(".rxq"), noinline)) ni_select(volatile u32 *mailbox, u8 *memory, u32 *request)
{
	if (!mailbox[38] || mailbox[30] >= 124 || mailbox[47]) return 0;
	volatile u32 *owners = (void *)(memory + 0xc400);
	static const u8 channels[8] = {0, 1, 1, 0, 2, 2, 3, 3};
	static const u8 indicators[8] = {0, 0, 1, 1, 0, 1, 0, 1};
	u32 *next_peer = (void *)(memory + NI_PEER_NEXT), *prev_peer = (void *)(memory + NI_PEER_PREV);
	u32 first = mailbox[170], node = first;
	do {
		u32 macid = node - 1;
		volatile u32 *ps = (void *)(memory + 0xe800 + macid * 16);
		u32 retired = !macid || (mailbox[62] == 128 || mailbox[62] == macid) ||
			!*(volatile u32 *)(memory + NI_PEER_ROUTE + macid * 4);
		for (u32 offset = 0; offset < 8; offset++) {
			u32 tid = (mailbox[112] + offset) & 7;
			u32 *head = (void *)(memory + NI_TID_HEAD + (macid * 8 + tid) * 4);
			if (!*head || (!retired && ps[0] && (ps[1] == ps[2] || !((ps[3] >> 16) & BIT(tid))))) continue;
			u32 buffer = *head, *waiting = uncached(buffer);
			u32 id = ni_owner(mailbox, memory), frames = 1;
			const u32 *peer = (void *)(memory + 0xd000 + macid * 32);
			u32 key = peer[5];
			u32 details = macid << 8 | ((key >> 16) & 127) << 17 | BIT(15);
			owners[id * 2] = buffer;
			owners[id * 2 + 1] = details;
			*(u32 *)(memory + NI_WAIT + id * 16 + 12) = tid;
			if (retired) {
				owners[id * 2 + 1] |= BIT(31);
				mailbox[47] = id + 1; mailbox[63]++;
			} else {
				for (u32 i = 0; i < 8; i++) request[i] = peer[i];
				request[0] = 256 + id; request[1] = channels[tid];
				request[2] = BIT(24) | (peer[2] & 0x3f0000) | (waiting[1] + ((peer[2] >> 16) & 63) + 8 - 14);
				request[4] = (peer[4] & ~0x1fffU) | tid | indicators[tid] << 12;
				request[5] = (peer[5] & ~255U) | *(u32 *)(memory + NI_PEER_BA + (macid * 8 + tid) * 4);
				request[8] = waiting[0]; request[9] = waiting[1];
				u32 caps = *(u32 *)(memory + 0xe200 + macid * 4);
				if (caps & BIT(17)) request[3] |= BIT(20);
				if (ps[0]) {
					request[4] |= (*(u32 *)(memory + NI_PEER_QUEUED + macid * 4) > 1 || (ps[3] & 2) ? BIT(19) : 0) | (ps[3] & 1 ? BIT(20) : 0);
					owners[id * 2 + 1] |= BIT(16);
					ps[2] = ps[1]; mailbox[39]++;
				} else {
					ps[2] = ps[1];
					u32 next = waiting[3] & ~127U, tail_id = id, body = waiting[1] + 8;
					if (((request[2] >> 16) & 63) == 26 && !(request[3] & BIT(19))) {
						while (next && frames < 8 && mailbox[30] + frames < 124) {
							u32 *part = uncached(next), pad = (0U - body) & 3;
							if (body + pad + part[1] + 8 > (caps >> 18)) break;
							u32 other = ni_owner(mailbox, memory);
							request[8 + frames * 2] = part[0]; request[9 + frames * 2] = part[1];
							body += pad + part[1] + 8;
							owners[other * 2] = next; owners[other * 2 + 1] = details;
							owners[tail_id * 2 + 1] = (owners[tail_id * 2 + 1] & ~BIT(15)) | other << 24;
							*(u32 *)(memory + NI_WAIT + other * 16 + 12) = tid;
							tail_id = other; next = part[3] & ~127U; frames++;
						}
					}
					if (frames > 1) {
						request[4] |= BIT(21);
						request[2] = frames << 24 | 26U << 16 | (26 + body);
						waiting[3] = tid | next;
					}
				}
			}
			*head = waiting[3] & ~127U;
			mailbox[112] = (tid + 1) & 7;
			if (!*head) *(u32 *)(memory + NI_TID_TAIL + (macid * 8 + tid) * 4) = 0;
			u32 *queued = (void *)(memory + NI_PEER_QUEUED + macid * 4);
			*queued -= frames; mailbox[38] -= frames; mailbox[30] += frames;
			(*(u32 *)(memory + NI_TID_QUEUED + (macid * 8 + tid) * 4)) -= frames;
			mailbox[170] = next_peer[macid];
			if (!*queued) {
				if (next_peer[macid] == node) mailbox[170] = 0;
				else {
					next_peer[prev_peer[macid] - 1] = next_peer[macid];
					prev_peer[next_peer[macid] - 1] = prev_peer[macid];
				}
			}
			ni_peer_event(mailbox, memory, macid);
			if (!retired) {
				(*(u32 *)(memory + 0x17000 + macid * 4)) += frames;
				(*(u32 *)(memory + 0x17200 + macid * 4)) |= BIT(tid);
				return frames;
			}
			return 0;
		}
		node = next_peer[macid];
	} while (node != first);
	return 0;
}
