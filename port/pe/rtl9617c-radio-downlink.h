/* SPDX-License-Identifier: GPL-2.0-only */
#define NI_WAIT 0x11000
#define NI_PEER_QUEUED 0x11800
#define NI_PEER_ROUTE 0x11a00
#define NI_PEER_BA 0x12000
#define NI_TID_QUEUED 0x13000
#define NI_TIM_MASK 0x14000
#define NI_TID_PENDING 0x18000
/* RX owns 0x17400..0x1782b and 0x1a000..0x1ac07 for its buffer pool. */
#define NI_PEER_NEXT 0x19000
#define NI_PEER_PREV 0x19200
#define NI_TID_HEAD 0x15000
#define NI_TID_TAIL 0x16000
#define NI_EPP_CONSUMERS 0x11c00
#define NI_EPP_RETIRE_TARGETS 0x19400
#define NI_TID_ACTIVE 0x19600
/* Match the software PS budget: four 64-frame ACs per peer, 512 total. */
#define NI_PS_PEER_LIMIT 256
#define NI_PS_TOTAL_LIMIT 512

static void __attribute__((noinline)) ni_peer_event(volatile u32 *mailbox, u8 *memory, u32 macid)
{
	u32 mask = 0;
	if (*(u32 *)(memory + 0xe800 + macid * 16))
#pragma clang loop unroll(disable)
		for (u32 tid = 0; tid < 8; tid++)
			if (*(u32 *)(memory + NI_TID_PENDING + (macid * 8 + tid) * 4)) mask |= BIT(tid);
	u32 *old = (void *)(memory + NI_TIM_MASK + macid * 4);
	if (*old != mask) { *old = mask; pe_event(mailbox); }
}

/* Queue metadata uses the NI BID headroom, before HEADER_A.
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

static void __attribute__((noinline)) ni_enqueue(
	volatile u32 *mailbox, u8 *memory, u32 queue, u32 head, u32 tail, u32 count)
{
	u32 macid = queue >> 3, *old_tail = (void *)(memory + NI_TID_TAIL + queue * 4);
	/* Interior links were filled while gathering; terminate only the tail. */
	*(u32 *)uncached(tail) = 0;
	if (*old_tail) *(u32 *)uncached(*old_tail) = head;
	else *(u32 *)(memory + NI_TID_HEAD + queue * 4) = head;
	*old_tail = tail;
	u32 *queued = (void *)(memory + NI_PEER_QUEUED + macid * 4);
	u32 *active = (void *)(memory + NI_TID_ACTIVE + macid * 4);
	if (!*queued) { ni_peer_add(mailbox, memory, macid); *active = 0; }
	*active |= BIT(queue & 7);
	*queued += count; mailbox[38] += count;
	*(u32 *)(memory + NI_TID_QUEUED + queue * 4) += count;
	if (macid) *(u32 *)(memory + NI_TID_PENDING + queue * 4) += count;
	ni_peer_event(mailbox, memory, macid);
}

static u32 ni_owner(volatile u32 *mailbox, u8 *memory)
{
	volatile u32 *owners = (void *)(memory + 0xc400);
	u32 id = mailbox[166];
	while (owners[id * 2]) id = (id + 1) & 127;
	mailbox[166] = (id + 1) & 127;
	return id;
}

static u32 __attribute__((section(".rxq"), noinline)) ni_ingest(volatile u32 *mailbox, u8 *memory)
{
	if (mailbox[47]) return 0;
	volatile u32 *ni = (void *)0xb0000000;
	u32 available = (ni[0x659c / 4] >> ((mailbox[114] - 0x7000) / 4)) & 255;
	if (!available) return 0;
	u32 cos = 0;
	if (available & 0xf0) { available >>= 4; cos = 4; }
	if (available & 0x0c) { available >>= 2; cos += 2; }
	if (available & 0x02) cos++;
	volatile u32 *consumers = (void *)(memory + NI_EPP_CONSUMERS);
	u32 consumer = consumers[cos], shift = mailbox[167], mask = (1U << shift) - 1;
	/* Stock ca_ni_rx_napi consumes a producer snapshot before publishing RD. */
	u32 producer = ni[mailbox[114] / 4 + cos] & mask;
	if (producer == consumer) return 0;
	u32 count = 0, group = 0, head = 0, tail = 0, grouped = 0;
	u32 cached_peer = 0, cached_low = 0, cached_high = 0;
	u32 fence = mailbox[171] & BIT(cos);
	u32 target = *(u32 *)(memory + NI_EPP_RETIRE_TARGETS + cos * 4), crossed = 0;
	/* PE1 owns queue counts; ARM changes EPP geometry only while paused. */
	u32 queued = mailbox[38], epp = mailbox[25] + (cos << shift);
	u32 now = mailbox[RTL_AX_NI_PS_NOW];
	sync();
	for (; count < 16 && consumer != producer; count++) {
		/* One excess arrival lets selection evict a sleeping queue while
		 * EPP keeps moving, rather than letting it retain the remaining EQ. */
		if (count && queued + grouped > NI_PS_TOTAL_LIMIT) break;
		const u32 *entry = uncached(epp + consumer);
		u32 descriptor = *(volatile u32 *)entry;
		if ((descriptor & 0x50) != 0x50) break;
		u32 buffer = descriptor & 0xffffff80, header, tid;
		u32 length = ni_length(entry, buffer, &header, &tid);
		u8 *ethernet = uncached(buffer + RTL_QM_HEADROOM + header);
		u32 macid = 0;
		if (length >= 14 && length <= RTL_QM_DATA_BYTES - header) {
			const u32 *destination = (const void *)ethernet;
			u32 low = destination[0], high = destination[1] & 65535;
			/* Cache only this batch's destination; admission can be revoked
			 * asynchronously and must still be checked for every packet. */
			if (cached_peer && low == cached_low && high == cached_high &&
			    *(volatile u32 *)(memory + NI_PEER_ROUTE + cached_peer * 4))
				macid = cached_peer;
			else {
				for (u32 peer = 1; peer < 128; peer++) {
					if (!*(u32 *)(memory + NI_PEER_ROUTE + peer * 4)) continue;
					const u32 *address = (const void *)(memory + 0xe400 + peer * 8);
					if (address[0] == low && (address[1] & 65535) == high) {
						macid = peer; break;
					}
				}
				cached_peer = macid; cached_low = low; cached_high = high;
			}
		}
		if (!macid) {
			mailbox[128] = descriptor; mailbox[129] = entry[1];
			mailbox[130] = length; mailbox[131] = header;
			mailbox[132] = big_word(ethernet); mailbox[133] = big_word(ethernet + 4); mailbox[40]++;
		}
		if (!macid) tid = 0;
		u32 queue = macid * 8 + tid;
		/* Link BIDs locally; update the shared peer/TID counts once per group. */
		if (grouped && queue != group) {
			queued += grouped;
			ni_enqueue(mailbox, memory, group, head, tail, grouped); grouped = 0;
		}
		/* Drop excess arrivals before linking them into a sleeping queue.
		 * The reserved discard WP retains the BID if EQ credit is absent. */
		if (macid && *(u32 *)(memory + 0xe800 + macid * 16) &&
		    (*(u32 *)(memory + NI_PEER_QUEUED + macid * 4) + grouped >= NI_PS_PEER_LIMIT ||
		     queued + grouped >= NI_PS_TOTAL_LIMIT)) {
			if (grouped) ni_enqueue(mailbox, memory, group, head, tail, grouped);
			grouped = 0;
			u32 id = ni_owner(mailbox, memory);
			u32 *owners = (void *)(memory + 0xc400);
			*(u32 *)uncached(buffer) = 0;
			owners[id * 2] = buffer; owners[id * 2 + 1] = BIT(31);
			mailbox[30]++; mailbox[47] = id + 1; mailbox[63]++;
			consumer = (consumer + 8) & mask;
			if (fence && consumer == target) crossed = 1;
			count++; break;
		}
		u32 *waiting = uncached(buffer);
		/* The queue link becomes the MPDU ownership chain when selected. */
		waiting[1] = length | header << 16;
		waiting[2] = now;
		if (grouped) *(u32 *)uncached(tail) = buffer;
		else { head = buffer; group = queue; }
		tail = buffer; grouped++;
		consumer = (consumer + 8) & mask;
		if (fence && consumer == target) crossed = 1;
	}
	if (grouped) ni_enqueue(mailbox, memory, group, head, tail, grouped);
	consumers[cos] = consumer; mailbox[26] = consumers[0];
	sync(); ni[mailbox[115] / 4 + cos] = consumer;
	/* A batch may pass the cutoff. Publish its queues and RD before ACK. */
	if (crossed) { sync(); mailbox[171] &= ~BIT(cos); }
	return count;
}


/* ARM publishes the generation and TID flags as one versioned command. */
static u32 __attribute__((section(".rxq"), noinline)) ni_ps_grant(
	u8 *memory, u32 macid, u32 *generation, u32 *flags, u32 *command)
{
	volatile u32 *version = (void *)(memory + RTL_AX_NI_PS_VERSION + macid * 4);
	volatile u32 *ps = (void *)(memory + 0xe800 + macid * 16);
	u32 before = *version;
	if (before & 1) return 0;
	sync();
	*generation = ps[1]; *flags = ps[3]; *command = before;
	sync();
	return before == *version;
}

static void __attribute__((section(".rxq"), noinline)) ni_ps_scan(volatile u32 *mailbox, u8 *memory)
{
	/* A late grant also needs service when the entire NI queue is empty. */
	u32 macid = mailbox[RTL_AX_NI_PS_SCAN] + 1, generation, flags, command;
	if (macid == 128) macid = 1;
	mailbox[RTL_AX_NI_PS_SCAN] = macid;
	if (!ni_ps_grant(memory, macid, &generation, &flags, &command)) return;
	volatile u32 *ps = (void *)(memory + 0xe800 + macid * 16);
	if (command == ps[2]) return;
	for (u32 tid = 0; tid < 8; tid++)
		if ((flags >> 16 & BIT(tid)) &&
		    *(u32 *)(memory + NI_TID_QUEUED + (macid * 8 + tid) * 4)) return;
	ps[2] = command;
	volatile u32 *record = (void *)(memory + RTL_AX_NI_PS_CANCEL + macid * 8);
	record[0] = generation;
	sync(); record[1]++;
	pe_event(mailbox);
}

static u32 __attribute__((section(".rxq"), noinline)) ni_select(volatile u32 *mailbox, u8 *memory, u32 *request)
{
	if (!mailbox[38]) return 0;
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
		u32 *active = (void *)(memory + NI_TID_ACTIVE + macid * 4), tids = *active;
		u32 generation = 0, grant_flags = 0, command = 0;
		if (!retired && !ni_ps_grant(memory, macid, &generation, &grant_flags, &command)) {
			node = next_peer[macid]; continue;
		}
		u32 tid = mailbox[112];
		/* Rotate the active set into the existing round-robin order. */
		for (tids = (tids >> tid | tids << (8 - tid)) & 255; tids;
		     tids >>= 1, tid = (tid + 1) & 7) {
			if (!(tids & 1)) continue;
			u32 *head = (void *)(memory + NI_TID_HEAD + (macid * 8 + tid) * 4);
			u32 buffer = *head, *waiting = uncached(buffer);
			u32 discard = retired || (ps[0] &&
				(*(u32 *)(memory + NI_PEER_QUEUED + macid * 4) > NI_PS_PEER_LIMIT ||
				 mailbox[38] > NI_PS_TOTAL_LIMIT ||
				 (int)(mailbox[RTL_AX_NI_PS_NOW] - waiting[2]) >=
				 (int)*(u32 *)(memory + RTL_AX_NI_PS_TIMEOUT + macid * 4)));
			if (discard && mailbox[47]) continue;
			if (!discard && (mailbox[30] >= 124 ||
			    (ps[0] && (command == ps[2] || !((grant_flags >> 16) & BIT(tid)))))) continue;
			u32 id = ni_owner(mailbox, memory), frames = 1;
			u32 tail_buffer = buffer, next_buffer = waiting[0];
			const u32 *peer = (void *)(memory + 0xd000 + macid * 32);
			u32 key = peer[5];
			u32 details = macid << 8 | ((key >> 16) & 127) << 17 | BIT(15);
			owners[id * 2] = buffer;
			owners[id * 2 + 1] = details;
			*(u32 *)(memory + NI_WAIT + id * 16 + 12) = tid;
			if (discard) {
				owners[id * 2 + 1] |= BIT(31);
				mailbox[47] = id + 1; mailbox[63]++;
			} else {
				u32 metadata = waiting[1], length = metadata & 65535;
#pragma clang loop unroll(disable)
				for (u32 i = 0; i < 8; i++) request[i] = peer[i];
				request[0] = 256 + id; request[1] = channels[tid];
				request[2] = BIT(24) | (request[2] & 0x3f0000) | (length + ((request[2] >> 16) & 63) + 8 - 14);
				request[4] = (request[4] & ~0x1fffU) | tid | indicators[tid] << 12;
				request[5] = (request[5] & ~255U) | *(u32 *)(memory + NI_PEER_BA + (macid * 8 + tid) * 4);
				request[8] = buffer + RTL_QM_HEADROOM + (metadata >> 16); request[9] = length;
				u32 caps = *(u32 *)(memory + 0xe200 + macid * 4);
				if (caps & BIT(17)) request[3] |= BIT(20);
				if (ps[0]) {
					request[4] |= (*(u32 *)(memory + NI_PEER_QUEUED + macid * 4) > 1 || (grant_flags & 2) ? BIT(19) : 0) | (grant_flags & 1 ? BIT(20) : 0);
					owners[id * 2 + 1] |= BIT(16);
					*(u32 *)(memory + RTL_AX_NI_PS_WP_GENERATION + id * 4) = generation;
					ps[2] = command; mailbox[39]++;
				} else {
					ps[2] = command;
					u32 next = next_buffer, body = length + 8;
					if (((request[2] >> 16) & 63) == 26 && !(request[3] & BIT(19))) {
						while (next && frames < 8) {
							u32 *part = uncached(next), pad = (0U - body) & 3;
							u32 next_metadata = part[1], next_length = next_metadata & 65535;
							if (body + pad + next_length + 8 > (caps >> 18)) break;
							request[8 + frames * 2] = next + RTL_QM_HEADROOM + (next_metadata >> 16);
							request[9 + frames * 2] = next_length;
							body += pad + next_length + 8;
							tail_buffer = next; next = part[0]; frames++;
						}
					}
					if (frames > 1) {
						request[4] |= BIT(21);
						request[2] = frames << 24 | 26U << 16 | (26 + body);
						next_buffer = next;
					}
				}
			}
			/* Detach the existing FIFO prefix; no per-subframe relinking. */
			*(u32 *)uncached(tail_buffer) = 0;
			*head = next_buffer;
			mailbox[112] = (tid + 1) & 7;
			if (!*head) {
				*(u32 *)(memory + NI_TID_TAIL + (macid * 8 + tid) * 4) = 0;
				*active &= ~BIT(tid);
			}
			u32 *queued = (void *)(memory + NI_PEER_QUEUED + macid * 4);
			/* Admission counts WPs, not the number of gathered MSDUs. */
			*queued -= frames; mailbox[38] -= frames; mailbox[30]++;
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
			if (discard && mailbox[78]) pe_event(mailbox);
			if (!discard) {
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
