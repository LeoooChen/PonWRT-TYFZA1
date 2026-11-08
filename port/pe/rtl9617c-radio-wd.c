/* SPDX-License-Identifier: GPL-2.0-only */
/* Native PE WD/WP/TXBD construction from host-owned packet requests. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
struct packed_word { u32 value; } __attribute__((packed));
#define BIT(n) (1U << (n))
#define REQUEST_BASE 0x4000
#define MAILBOX_OFFSET 0x2000
#define TX_BURST 8
#include "rx-geometry.h"
#include "tx-ownership.h"
#include "traffic-flow.h"
#include "amsdu-queue.h"
#include "qos.h"
#include <linux/rtl9617c-qm-layout.h>

static inline void sync(void) { __asm__ volatile("sync" ::: "memory"); }
static inline void *uncached(u32 address) { return (void *)(address | 0xa0000000U); }
static void word(u8 *out, u32 value)
{
	/* WD and TXBD words are aligned; use one DDR transaction, not four. */
	*(u32 *)out = value;
}
static void unaligned_word(u8 *out, u32 value)
{
	((struct packed_word *)out)->value = value;
}

#ifdef PE_UPLINK
static u32 __attribute__((section(".entry"), noinline)) mac_equal(const u8 *a, const u8 *b)
{
	return ((const struct packed_word *)a)->value == ((const struct packed_word *)b)->value &&
		a[4] == b[4] && a[5] == b[5];
}
#endif

static void address(u8 *out, u32 dma, u32 bytes, u32 last)
{
	out[0] = bytes;
	out[1] = bytes >> 8 | last << 7;
	unaligned_word(out + 2, dma);
}

#ifdef PE_UPLINK
static u32 big_word(const u8 *data)
{
	return __builtin_bswap32(*(const u32 *)data);
}
#endif

static u32 ni_length(const u32 *entry, u32 buffer, u32 *head, u32 *tid)
{
	/* Reuse Header1 for length and TID; compact EPP needs only its TID. */
	u32 epp = entry[1];
	const u32 *data = uncached(buffer + RTL_QM_HEADROOM);
	if ((epp >> 14) & 63) {
		if (tid) *tid = (data[1] >> 24) & 7;
		*head = 16; return (epp & 0x3fff) - 16;
	}
	u32 extra = data[0] & 128 ? 8 : 0, raw = data[1];
	if (tid) *tid = (raw >> 24) & 7;
	*head = 8 + extra;
	/* Extract the network-order field without a full MIPS32 byte swap. */
	return ((raw & 31) << 9 | ((raw >> 7) & 510) | ((raw >> 23) & 1)) - extra;
}

static void amsdu_prefix(u8 *ethernet, u32 bytes)
{
	/* Native NI Ethernet begins on a word boundary. Preserve all address
	 * words before the overlapping prepend; EtherType stays in place. */
	const u32 *in = (const void *)ethernet;
	u32 a = in[0], b = in[1], c = in[2], length = bytes - 6;
	u32 *out = (void *)(ethernet - 8);
	out[0] = a; out[1] = b; out[2] = c;
	out[3] = 0xaaaa0000U | (length & 255) << 8 | length >> 8;
	out[4] = 3;
}

static void header_copy(u8 *out, const u8 *in, u32 length)
{
	u32 i = 0;
	for (; i + 4 <= length; i += 4)
		unaligned_word(out + i, ((const struct packed_word *)(in + i))->value);
	for (; i < length; i++) out[i] = in[i];
}

static void pe_event(volatile u32 *mailbox)
{
	mailbox[67]++;
}

static void __attribute__((section(".entry"), noinline)) pe_event_flush(volatile u32 *mailbox)
{
	if (!mailbox[64]) return;
	u32 *activity = (void *)((u8 *)mailbox - 0x2000 + RTL_AX_NI_TX_NOTIFY_TIME);
	if (activity[1]) {
		u32 now = *(volatile u32 *)(mailbox[2] + 0xc438);
		/* ARM's HZ=100 last_tx clock cannot use sub-tick refreshes. */
		if ((u32)(now - activity[0]) >= 10000) {
			activity[0] = now; activity[1] = 0; pe_event(mailbox);
		}
	}
	if (mailbox[69] != mailbox[66] || mailbox[69] == mailbox[67]) return;
	volatile u32 *timer = (void *)mailbox[64];
	mailbox[69] = mailbox[67];
	sync();
	/* PER_TMR_LOADE bit4 transfers LD4 to CNT4; roll mode alone does not. */
	timer[1] = 0;
	sync();
	timer[0] = 1;
	timer[8] = BIT(4);
	timer[3] = 1;
	timer[1] = 0x80;
}

#ifndef PE_UPLINK
static void ni_buffered_event(volatile u32 *mailbox, u32 buffered)
{
	if (mailbox[65] == buffered) return;
	mailbox[65] = buffered;
	pe_event(mailbox);
}

#endif

static void __attribute__((section(".entry"), noinline)) rpq_event(volatile u32 *mailbox)
{
	pe_event(mailbox);
}

/* PE1 is the only RPQ consumer for a radio; publish the record before count. */
static void pe_rpq_fault(u8 *memory, u32 reason, u32 info, u32 wd,
			 u32 report, u32 owner, u32 details, u32 consumer)
{
	volatile u32 *trace = (void *)(memory + RTL_AX_RPQ_FAULT_TRACE);
	u32 sequence = trace[0] + 1;
	volatile u32 *entry = trace + 1 + ((sequence - 1) & (RTL_AX_RPQ_FAULT_SLOTS - 1)) * RTL_AX_RPQ_FAULT_WORDS;

	entry[0] = 0;
	/* Invalidate an overwritten slot before any replacement fields reach ARM. */
	sync();
	entry[1] = reason; entry[2] = info; entry[3] = wd; entry[4] = report;
	entry[5] = owner; entry[6] = details; entry[7] = consumer;
	sync(); entry[0] = sequence;
	sync(); trace[0] = sequence;
}

static u32 ni_owner_available(u8 *memory)
{
	u32 *state = (void *)(memory + RTL_AX_TX_OWNER_STATE);
	return state[0] != state[1];
}
static u32 ni_owner(u8 *memory)
{
	u32 *state = (void *)(memory + RTL_AX_TX_OWNER_STATE);
	u32 id = ((u16 *)(memory + RTL_AX_NI_FREE))[state[0] & (RTL_AX_NI_REQUESTS - 1)];
	state[0]++;
	return id;
}

/* Same ownership key as factory wd_ring[channel].wp_tag[sequence]. */
static u32 pe_wp_available(u8 *memory, u32 ring)
{
	u32 *state = (void *)(memory + RTL_AX_TX_OWNER_STATE);
	u32 *map = (void *)(memory + RTL_AX_WP_TAG_MAP + ring * RTL_AX_WP_TAG_STRIDE * 4);
	if (!ring) return (state[2] + 1) % RTL_AX_WP_TAGS != state[3];
	u32 first = state[4 + ring], tag = first;
	do {
		if (!map[tag]) { state[4 + ring] = tag; return 1; }
		tag = tag + 1 == RTL_AX_WP_TAGS ? 1 : tag + 1;
	} while (tag != first);
	return 0;
}
static u32 pe_wp_take(u8 *memory, u32 ring, u32 id)
{
	u32 *state = (void *)(memory + RTL_AX_TX_OWNER_STATE);
	u16 *free = (void *)(memory + RTL_AX_WP_FREE);
	u32 tag = ring ? state[4 + ring] : free[state[2]];
	*(u32 *)(memory + RTL_AX_WP_TAG_MAP + (ring * RTL_AX_WP_TAG_STRIDE + tag) * 4) = id + 1;
	if (ring) state[4 + ring] = tag + 1 == RTL_AX_WP_TAGS ? 1 : tag + 1;
	else state[2] = (state[2] + 1) % RTL_AX_WP_TAGS;
	if (rtl_ax_tx_id_is_ni(id))
		*(u32 *)(memory + RTL_AX_NI_COOKIES + (id - RTL_AX_NI_ID_BASE) * 4) = ring << 12 | tag;
	return tag;
}
static void pe_wp_release(u8 *memory, u32 cookie)
{
	u32 ring = cookie >> 12, tag = cookie & 4095;
	*(u32 *)(memory + RTL_AX_WP_TAG_MAP + (ring * RTL_AX_WP_TAG_STRIDE + tag) * 4) = 0;
	if (!ring) {
		u32 *state = (void *)(memory + RTL_AX_TX_OWNER_STATE);
		((u16 *)(memory + RTL_AX_WP_FREE))[state[3]] = tag;
		state[3] = (state[3] + 1) % RTL_AX_WP_TAGS;
	}
}
static u32 pe_report_ring(u32 report)
{
	u32 qsel = (report >> 8) & 31;
	return qsel < 8 ? qsel & 3 : qsel == 0x12 ? 4 : qsel == 0x11 ? 5 : 6;
}
static u32 pe_report_id(u8 *memory, u32 report)
{
	u32 ring = pe_report_ring(report), tag = (report >> 16) & 4095;
	if (ring == 6 || tag == RTL_AX_WP_TAG_INVALID) return ~0U;
	u32 owner = *(u32 *)(memory + RTL_AX_WP_TAG_MAP + (ring * RTL_AX_WP_TAG_STRIDE + tag) * 4);
	return owner - 1;
}

static void pe_rpq(volatile u32 *mailbox, u8 *memory)
{
	if (!mailbox[68]) return;
	volatile u32 *bar = (void *)mailbox[2];
	u32 consumer = mailbox[70], producer = mailbox[73];
	volatile u32 *buffer = uncached(mailbox[68] + consumer * RTL_AX_RPQ_BUFFER_BYTES);
	u32 info = buffer[0], tag = mailbox[71] == 8191 ? 1 : mailbox[71] + 1;
	if (((info >> 16) & 8191) != tag) return;
	if (((bar[0x121c / 4] >> 16) & 1023) == consumer) return;
	sync();
	u32 wd = buffer[1], type = (wd >> 24) & 15, bytes = wd & 0x3fff;
	u32 offset = 4 + (wd & BIT(31) ? 32 : 16) + ((wd >> 14) & 3) * 2 + ((wd >> 28) & 7) * 16;
	if ((wd & BIT(23)) && type != 1) offset += 4;
	u32 written = info & 0x3fff, forwarded = 0, completed = mailbox[75];
	u32 reports = 0, failures = 0;
	u32 completion_start = mailbox[45], completion_write = completion_start;
	u32 old = rtl_ax_rx_epoch_old(&mailbox[RTL_AX_PE_DFS_EPOCH + 1], consumer);
	u8 *out = uncached(mailbox[72] + producer * RTL_AX_RPQ_BUFFER_BYTES);
	if ((info & 0xc000) != 0xc000 || written > RTL_AX_RPQ_BUFFER_BYTES || offset > written || bytes > written - offset) {
		pe_rpq_fault(memory, 1, info, wd, 0, 0, 0, consumer);
		mailbox[77]++;
	} else {
		/* Stock dispatch recycles WPs separately from ordinary RX delivery.
		 * Reserve ARM space only for reports that actually need it. Check
		 * the whole packet before completing any WP in a mixed report. */
		if (((producer + 1) & 255) == mailbox[74] && !(type == 8 && old)) {
			if (type != 7) return;
			for (u32 i = 0; i + 4 <= bytes; i += 4) {
				u32 report = ((const struct packed_word *)((u8 *)buffer + offset + i))->value;
				u32 id = pe_report_id(memory, report);
				if (!rtl_ax_tx_id_is_ni(id)) return;
				volatile u32 *owner = (void *)(memory + RTL_AX_NI_OWNERS + (id - RTL_AX_NI_ID_BASE) * 8);
				u32 details = owner[1];
				if (owner[0] && !(details & BIT(31)) &&
				    ((details >> 8) & 127) == (report & 127) && (details & BIT(16))) return;
			}
		}
		if (type == 8 && old) { /* Old channel's radar report. */ }
		else if (type != 7) { header_copy(out, (const u8 *)buffer, written); forwarded = bytes; }
		else for (u32 i = 0; i + 4 <= bytes; i += 4) {
			u32 report = ((const struct packed_word *)((u8 *)buffer + offset + i))->value;
			u32 id = pe_report_id(memory, report), status = (report >> 13) & 7;
			if (id == ~0U) {
				pe_rpq_fault(memory, 2, info, wd, report, 0, 0, consumer);
				mailbox[77]++; continue;
			}
			if (!rtl_ax_tx_id_is_ni(id)) {
				if (!forwarded) header_copy(out, (const u8 *)buffer, offset);
				unaligned_word(out + offset + forwarded, (report & ~0x0fff0000U) | id << 16); forwarded += 4;
				pe_wp_release(memory, pe_report_ring(report) << 12 | ((report >> 16) & 4095));
				continue;
			}
			volatile u32 *owners = (void *)(memory + RTL_AX_NI_OWNERS);
			u32 member = id - RTL_AX_NI_ID_BASE, details = owners[member * 2 + 1];
			if (!owners[member * 2] || details & BIT(31) ||
			    ((details >> 8) & 127) != (report & 127)) {
				pe_rpq_fault(memory, 2, info, wd, report, owners[member * 2], details, consumer);
				mailbox[77]++; continue;
			}
			if (details & BIT(16)) {
				u32 generation = *(u32 *)(memory + RTL_AX_NI_GENERATIONS + member * 4);
				volatile u32 *ps = (void *)(memory + 0xe800 + (report & 127) * 16);
				/* A late old RPQ must not overwrite the current grant's completion. */
				if (generation == ps[1]) {
					volatile u32 *record = (void *)(memory + RTL_AX_NI_PS_DONE + (report & 127) * 8);
					record[0] = generation;
					sync(); record[1]++;
				}
				if (!forwarded) header_copy(out, (const u8 *)buffer, offset);
				unaligned_word(out + offset + forwarded, (report & ~0x0fff0000U) | id << 16); forwarded += 4;
			}
			owners[member * 2 + 1] = details | BIT(31) | status;
			*(u32 *)(memory + RTL_AX_NI_COMPLETIONS + completion_write * 4) = member;
			completion_write = (completion_write + 1) & RTL_AX_NI_COMPLETION_MASK;
			reports++; failures += !!(status || (report & BIT(31)));
		}
		if (forwarded) {
			word(out + 4, (wd & ~0x3fffU) | forwarded);
			sync();
			mailbox[73] = (producer + 1) & 255;
			if (producer == mailbox[74]) rpq_event(mailbox);
		}
	}
	if (completion_write != completion_start) {
		/* PE1 alone accounts NI completions; publish totals once per RPQ. */
		mailbox[75] = completed + reports;
		if (failures) mailbox[76] += failures;
		/* Publish completed owners before the local recycler consumes them. */
		sync();
		mailbox[45] = completion_write;
	}
	buffer[0] = 0;
	mailbox[70] = (consumer + 1) & 255;
	mailbox[71] = tag;
	sync();
	*(volatile u16 *)((u8 *)bar + 0x121c) = mailbox[70];
	bar[0x10b4 / 4] = BIT(2);
	/* A peer/key waiter needs its completion even while other peers keep TX busy. */
	if (mailbox[78] && mailbox[75] != completed) rpq_event(mailbox);
}

/* NI owns coherent Ethernet BIDs. Build their final wire image directly;
 * host DMA cookies below must remain opaque to the PE CPU. */
static u32 ni_build_payload(volatile u32 *mailbox, u8 *memory,
	const u32 *request, u8 *page)
{
	u32 flags = request[4], macid = flags >> 24, tid = flags & 7;
	u32 frames = request[2] >> 24, header_bytes = (request[2] >> 16) & 63;
	u32 *sequences = (void *)(memory +
#ifdef PE_UPLINK
		0x10000
#else
		0x3000
#endif
	);
	u32 sequence = sequences[macid * 8 + tid];
	sequences[macid * 8 + tid] = (sequence + 1) & 0xfff;
	const u32 *ethernet = uncached(request[8]);
	u32 da0 = ethernet[0], addresses = ethernet[1], sa = ethernet[2];
	u32 ap0 = mailbox[16], ap1 = mailbox[17];
	u32 amsdu = !!(flags & BIT(21)), client = !!(flags & BIT(23));
	const u32 *bssid = (const void *)(memory + 0xe400 + macid * 8);
	u32 ra0 = client ? bssid[0] : da0;
	u32 ra1 = client ? bssid[1] & 65535 : addresses & 65535;
	u32 addr30 = client ? (amsdu ? ra0 : da0) : (amsdu ? ap0 : addresses >> 16 | sa << 16);
	u32 addr31 = client ? (amsdu ? ra1 : addresses & 65535) : (amsdu ? ap1 & 65535 : sa >> 16);
	u32 header_words[9];
	u8 *header = (void *)header_words;
	header_words[0] = (header_bytes == 26 ? 0x88 : 0x08) | (client ? 0x0100 : 0x0200) |
		(flags & (BIT(15) | BIT(16)) ? 0x4000 : 0) |
		(flags & BIT(19) ? 0x2000 : 0);
	header_words[1] = ra0;
	header_words[2] = ra1 | ap0 << 16;
	header_words[3] = ap0 >> 16 | ap1 << 16;
	header_words[4] = addr30;
	header_words[5] = addr31 | sequence << 20;
	header_words[6] = tid | (flags & BIT(20) ? 0x10 : 0) | (amsdu ? 0x80 : 0);
	if (amsdu) {
		u32 pad = 0;
		for (u32 n = 0; n < frames; n++) {
			u32 input = request[8 + n * 2], bytes = request[9 + n * 2];
			u32 output = input - (n ? 8 + pad : 34);
			amsdu_prefix(uncached(input), bytes);
			if (!n) {
				u8 *out = uncached(output);
				/* Headroom puts the WLAN header at word offset two. */
				out[0] = header[0]; out[1] = header[1];
				for (u32 i = 2; i < 26; i += 4)
					*(u32 *)(out + i) = ((const struct packed_word *)(header + i))->value;
			} else for (u32 i = 0; i < pad; i++) ((u8 *)uncached(output))[i] = 0;
			address(page + 64 + n * 6, output, bytes + (n ? 8 + pad : 34), n + 1 == frames);
			pad = (0U - bytes - 8) & 3;
		}
		mailbox[57]++;
	} else {
		u8 *llc = header + header_bytes;
		llc[0] = llc[1] = 0xaa; llc[2] = 3;
		llc[3] = llc[4] = llc[5] = 0;
		llc[6] = ((const u8 *)ethernet)[12]; llc[7] = ((const u8 *)ethernet)[13];
		u32 output = request[8] + 14 - header_bytes - 8;
		header_copy(uncached(output), header, header_bytes + 8);
		address(page + 64, output, request[2] & 0x3fff, 1);
	}
	mailbox[18] = request[0]; mailbox[19] = flags; mailbox[20] = request[2];
	mailbox[21] = request[8];
	return sequence;
}

static u32 __attribute__((section(".rxq"), noinline)) pe_build_tx(
	volatile u32 *mailbox, u8 *memory, const u32 *request, u32 *next_producer)
{
	static const u32 channels[] = { 0, 1, 2, 3, 8, 9 };
	u32 ni_packet = rtl_ax_tx_id_is_ni(request[0]), ni_frames = ni_packet ? request[2] >> 24 : 1;
	u32 id = request[0], ring_info = request[1], packet = request[2];
	u32 rate = request[3], flags = request[4], aggregation = request[5];
	u32 ring = ring_info & 7, slot = (ring_info >> 8) & 255, next = (ring_info >> 16) & 255;
	if (mailbox[24]) { slot = mailbox[32 + ring]; next = (slot + 1) & 255; }
	u32 wd_slot = mailbox[RTL_AX_TX_WD_NEXT + ring];
	u32 wd_page = ring * RTL_AX_TX_WD_PER_RING + wd_slot;
	u32 segments = packet >> 24, count = 0, used = 64;
	u32 aggregate = aggregation & 255;
	if (ni_packet) *(u32 *)(memory + RTL_AX_NI_RATES + (id - RTL_AX_NI_ID_BASE) * 4) = rate;
	/* Construct privately, then publish the final DMA image. Neither the
	 * descriptor nor an in-place NI header needs a DDR scratch round trip. */
	u32 page_words[32], header_words[16];
	u8 *page = (void *)page_words, *header = (void *)header_words;
	u8 *txbd = uncached(mailbox[11] + (ring * 256 + slot) * 8);
	u32 sequence = flags & 0xfff;

	/* All active fields are assigned below. Clear only reserved WD words
	 * and WP padding, preserving the original descriptor format. */
	word(page + 24, 0); word(page + 44, 0);
	word(page + 52, 0); word(page + 60, 0);
	if (ni_packet) {
		sequence = ni_build_payload(mailbox, memory, request, page);
		count = ni_frames; used = 64 + count * 6;
	} else {
		if (flags & BIT(22)) {
			/* Original normal and shortcut TX share sta->txseq_tid[]. */
			u32 macid = flags >> 24, tid = flags & 7;
			u32 header_bytes = (packet >> 16) & 63;
			u32 *sequences = (void *)(memory +
#ifdef PE_UPLINK
				0x10000
#else
				0x3000
#endif
			);
			sequence = sequences[macid * 8 + tid];
			sequences[macid * 8 + tid] = (sequence + 1) & 0xfff;
			header_copy(header, uncached(mailbox[14] + id * 128), header_bytes);
			header[22] = sequence << 4;
			header[23] = sequence >> 4;
			address(page + used, mailbox[14] + id * 128, header_bytes, 0);
			used += 6; count++;
		}
		if (flags & BIT(18)) {
			u32 macid = flags >> 24, tid = flags & 7;
			u32 header_bytes = (packet >> 16) & 63;
			const struct packed_word *input = uncached(mailbox[14] + id * 128);
			/* Gather addresses once; byte assembly belongs in private DMEM. */
			u32 ethernet_words[4] = {input[0].value, input[1].value,
				input[2].value, ((const u8 *)input)[12] | (u32)((const u8 *)input)[13] << 8};
			u32 ap_words[2] = {mailbox[16], mailbox[17]};
			u8 *ethernet = (void *)ethernet_words, *ap_address = (void *)ap_words;
			const u8 *bssid = memory + 0xe400 + macid * 8;
			u32 client = !!(flags & BIT(23));
			u32 *sequences = (void *)(memory +
#ifdef PE_UPLINK
				0x10000
#else
				0x3000
#endif
			);
			u32 protected = !!(flags & (BIT(15) | BIT(16)));

			sequence = sequences[macid * 8 + tid];
			sequences[macid * 8 + tid] = (sequence + 1) & 0xfff;
			header[0] = header_bytes == 26 ? 0x88 : 0x08;
			header[1] = (client ? 0x01 : 0x02) | protected << 6 | (flags & BIT(19) ? 0x20 : 0);
			header[2] = header[3] = 0;
#pragma clang loop unroll(disable)
			for (u32 i = 0; i < 6; i++) {
				header[4 + i] = client ? bssid[i] : ethernet[i];
				header[10 + i] = ap_address[i];
				header[16 + i] = client ? (flags & BIT(21) ? bssid[i] : ethernet[i]) :
					(flags & BIT(21) ? ap_address[i] : ethernet[6 + i]);
			}
			header[22] = sequence << 4;
			header[23] = sequence >> 4;
			if (header_bytes == 26) { header[24] = tid | (flags & BIT(20) ? 0x10 : 0) | (flags & BIT(21) ? 0x80 : 0); header[25] = 0; }
			u8 *llc = header + header_bytes;
			llc[0] = llc[1] = 0xaa; llc[2] = 3;
			llc[3] = llc[4] = llc[5] = 0;
			llc[6] = ethernet[12]; llc[7] = ethernet[13];
			address(page + used, mailbox[14] + id * 128, header_bytes + (flags & BIT(21) ? 0 : 8), 0);
			used += 6; count++;
			mailbox[18] = id; mailbox[19] = flags; mailbox[20] = packet;
			mailbox[21] = request[8];
		}
		for (u32 segment = 0; segment < segments; segment++) {
			u32 address = request[8 + segment * 2];
			u32 bytes = request[9 + segment * 2];
			if (!segment && flags & BIT(18)) { address += 14; bytes -= 14; }
			if (!segment && flags & BIT(22)) {
				u32 header_bytes = (packet >> 16) & 63;
				address += header_bytes; bytes -= header_bytes;
			}
			while (bytes) {
				u32 length = bytes > 2047 ? 2047 : bytes;
				u32 last = segment + 1 == segments && bytes == length;
				page[used] = length;
				page[used + 1] = length >> 8 | last << 7;
				unaligned_word(page + used + 2, address);
				used += 6; count++; address += length; bytes -= length;
			}
		}
	}
	/* A header-only ARM frame also needs a terminating address entry. */
	page[used - 5] |= 0x80;
	/* Address entries are six bytes; keep the rounded final DDR word zero. */
	page[used] = page[used + 1] = 0;
	u32 dw0 = BIT(7) | channels[ring] << 16 | BIT(22) |
		(((packet >> 16) & 63) / 2) << 11 | ((flags & BIT(13)) ? BIT(31) : 0);
	u32 dw1 = count << 26, dw4 = 0, dw5 = 0;
	u32 dw10 = aggregate ? ((aggregation >> 8) & 7) << 18 : 0;
	if (flags & (BIT(15) | BIT(16))) {
		dw0 |= 1U << 24; /* Eight-byte CCMP IV inserted by the chip. */
		dw1 |= 6 | ((aggregation >> 24) & 3) << 4;
		dw10 |= BIT(8) | ((aggregation >> 16) & 127);
		if (flags & BIT(16)) dw0 |= BIT(6);
		else {
			dw1 |= BIT(6);
			dw4 = request[6] << 16;
			dw5 = request[6] >> 16 | request[7] << 16;
		}
	}
	word(page, dw0); word(page + 4, dw1);
	word(page + 8, (flags & 0x7f000000) | (ring < 4 ? ring : ring == 4 ? 0x12 : 0x11) << 17 |
		((flags & BIT(12)) ? BIT(23) : 0) | (packet & 0x3fff));
	/* C peer preload avoids the reproduced mixed-length stall. Without it,
	 * retain the old length boundary guard against zero-packet TX stalls.
	 * Record every descriptor, including ARM frames sharing this DMA ring. */
	u32 context = (flags & 0x7f000000) | (flags & BIT(12)) << 11;
	u32 previous = mailbox[197 + ring], bytes = packet & 0x3fff;
	u32 boundary = (previous & ~0x3fffU) != context ||
		(!(aggregation & RTL_AX_TX_AGG_PRELOAD) && bytes < (previous & 0x3fff));
	mailbox[197 + ring] = context | bytes;
	word(page + 12, sequence | (aggregate ? BIT(12) : BIT(13)) |
		(boundary ? BIT(13) : 0));
	word(page + 16, dw4); word(page + 20, dw5);
	word(page + 28, (rate & BIT(20) ? 0 : BIT(31)) | ((rate >> 16) & 3) << 28 |
		((rate >> 12) & 7) << 25 | (rate & 0x1ff) << 16);
	/* Original DATA metadata enables fallback for firmware-selected rates. */
	word(page + 32, (rate & BIT(20) ? 0 : BIT(9) | BIT(10)) |
		(rate & BIT(21) ? BIT(11) : 0));
	/* Original TXSC keeps oversized MPDU retries within their PHY family. */
	word(page + 36, ((flags & BIT(14)) ? BIT(11) : 0) | (aggregate ? (aggregate - 1) & 255 : 0) |
		((packet & 0x3fff) > 2300 ? (rate & 0x180U) << 16 : 0));
	word(page + 40, dw10);
	word(page + 48, rate & BIT(22) ? BIT(27) | BIT(31) : 0);
	if (flags & BIT(17)) {
		u16 checksum = 0;
#pragma clang loop unroll(disable)
		for (u32 i = 0; i < 14; i++) checksum ^= page_words[i] ^ (page_words[i] >> 16);
		page[16] = checksum; page[17] = checksum >> 8;
	}
	word(page + 56, 0x8000 | pe_wp_take(memory, ring, id) | ((flags & BIT(12)) ? BIT(14) : 0));
	if (!ni_packet && (flags & (BIT(18) | BIT(22))))
		header_copy(uncached(mailbox[14] + id * 128), header,
			((packet >> 16) & 63) + (flags & BIT(18) ? 8 : 0));
	/* RPQ releases payload/WP ownership, not PCIe's retained WD page. */
	u32 *published = uncached(mailbox[12] + wd_page * 128);
	for (u32 i = 0; i < (used + 3) / 4; i++) published[i] = page_words[i];
	word(txbd, BIT(30) | used); word(txbd + 4, mailbox[12] + wd_page * 128);
	mailbox[RTL_AX_TX_WD_NEXT + ring] = wd_slot + 1 == RTL_AX_TX_WD_PER_RING ? 0 : wd_slot + 1;
	mailbox[RTL_AX_TX_LAST_WD] = wd_page;
	/* The batch publisher orders all DMA writes before its doorbells. */
	*next_producer = next;
	if (mailbox[24]) mailbox[32 + ring] = next;
	if (ni_packet) {
		mailbox[28] += ni_frames; mailbox[56]++;
		u32 *stats = (void *)(memory + RTL_AX_TX_PEER_STATS + ((flags >> 24) & 127) * 8);
		stats[0] += (packet & 0x3fff) - ((packet >> 16) & 63) - 8;
		stats[1] += ni_frames;
		u32 queue = ((flags >> 24) & 127) * 8 + (flags & 7);
		(*(u32 *)(memory + RTL_AX_NI_TX_ACTIVITY + queue * 4)) += ni_frames;
		/* Flush the last sparse packet too, without waiting for another TX. */
		*(u32 *)(memory + RTL_AX_NI_TX_NOTIFY_TIME + 4) = 1;
	}
	mailbox[10]++;
	mailbox[RTL_AX_PE_TX_BYTES] += packet & 0x3fff;
	mailbox[1]++;
	mailbox[13] = id;
	return ring;
}

#ifdef PE_UPLINK
static void __attribute__((section(".rxq"), noinline)) pe_tx_publish(volatile u32 *mailbox, u32 budget, u32 requests[16][32])
{
	static const u32 indices[] = { 0x1058, 0x105c, 0x1060, 0x1064, 0x1078, 0x107c };
	u32 read = mailbox[RTL_AX_TX_PUBLISH_READ], write = mailbox[RTL_AX_TX_PUBLISH_WRITE];
	u32 mask = 0, producers[6], checked = 0, room[6];
	u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
	if (read == write) return;
	/* Requests are private to PE1, not an inter-engine handoff. */
	while (read != write && budget--) {
		const u32 *request = requests[read];
		u32 next, ring = request[1] & 7;
		/* Factory hal_get_avail_txbd checks the shared channel before a
		 * batch: ARM and NI WP limits alone do not bound its TXBD use. */
		if (!(checked & BIT(ring))) {
			volatile u32 *bar = (void *)mailbox[2];
			u32 hardware = (bar[indices[ring] / 4] >> 16) & 255;
			u32 producer = mailbox[24] ? mailbox[32 + ring] : (request[1] >> 8) & 255;
			room[ring] = (hardware - producer - 1) & 255;
			checked |= BIT(ring);
		}
		if (!room[ring] || !pe_wp_available(memory, ring)) break;
		room[ring]--;
		pe_build_tx(mailbox, memory, request, &next);
		producers[ring] = next; mask |= BIT(ring);
		read = (read + 1) & 15;
	}
	/* Stock update_txbd finishes the WD and ownership before trigger_txdma. */
	sync();
	for (u32 ring = 0; ring < 6; ring++) {
		if (!(mask & BIT(ring))) continue;
		/* Complete the posted DDR batch before PCIe can fetch its WDs. */
		u32 last = (producers[ring] - 1) & 255;
		(void)*(volatile u32 *)uncached(mailbox[11] + (ring * 256 + last) * 8 + 4);
		sync();
		*(volatile u16 *)(mailbox[2] + indices[ring]) = producers[ring];
	}
	sync();
	mailbox[RTL_AX_TX_PUBLISH_READ] = read;
}

#include "rtl9617c-radio-uplink.h"
#include "rtl9617c-radio-downlink.h"
#else
static void __attribute__((section(".rxq"), noinline)) pe_rxq(volatile u32 *mailbox)
{
	if (!mailbox[80]) return;
	volatile u32 *bar = (void *)mailbox[2];
	u32 returned = mailbox[83], scan = mailbox[81];
	/* ARM finishes its synchronous frame callback before releasing this slot.
	 * Only PE clears the tag and returns the physical RXBD to PCIe. */
	while (returned != mailbox[84]) {
		*(volatile u32 *)uncached(mailbox[80] + returned * 12288) = 0;
		returned = (returned + 1) & 255;
	}
	if (returned != mailbox[83]) {
		mailbox[83] = returned;
		sync();
		*(volatile u16 *)((u8 *)bar + 0x1218) = returned;
	}
	if (((bar[0x1218 / 4] >> 16) & 1023) == scan) return;
	u32 info = *(volatile u32 *)uncached(mailbox[80] + scan * 12288);
	u32 tag = mailbox[82] == 8191 ? 1 : mailbox[82] + 1;
	if (((info >> 16) & 8191) != tag) return;
	mailbox[82] = tag;
	mailbox[86]++;
	sync();
	mailbox[81] = (scan + 1) & 255;
	if (scan == mailbox[84]) pe_event(mailbox);
}
#endif

static void __attribute__((section(".rxq"), noinline)) ni_recycle(volatile u32 *mailbox, u8 *memory)
{
	volatile u32 *ni = (void *)0xb0000000;
	volatile u32 *owners = (void *)(memory + RTL_AX_NI_OWNERS);
	/* ARM changes the EQ/CPU assignment only while this radio is paused. */
	u32 eq = mailbox[117];
	volatile u32 *push = ni + mailbox[116] / 4;
	u32 recycled = 0, released = 0, credit = 0;

	for (;;) {
		u32 dropped = mailbox[47], consumer = mailbox[46];
		if (!dropped && consumer == mailbox[45]) break;
		sync();
		u32 id = dropped ? dropped - 1 : *(u32 *)(memory + RTL_AX_NI_COMPLETIONS + consumer * 4);
		u32 buffer = owners[id * 2], returned = 0;
		/* PE1 alone pushes to this EQ. Carry unused credit across this
		 * call's MPDUs; every accepted PUSH consumes one sampled BID. */
		if (!credit)
			credit = ni[(0x6328 + eq * 4) / 4] & 0x3fff;
		/* A WP owns one completed MPDU. Account its BID chain together,
		 * retaining the unreturned suffix when either hardware credit stalls. */
		do {
			if (!credit || !(push[0] & BIT(31))) break;
			/* The owner acquire or preceding PUSH fence orders this link. */
			u32 next = *(u32 *)uncached(buffer);
			push[1] = buffer | eq;
			buffer = next; returned++; credit--;
			sync();
		} while (buffer);
		if (!returned) break;
		owners[id * 2] = buffer;
		recycled += returned;
#ifdef PE_UPLINK
		u32 macid = (owners[id * 2 + 1] >> 8) & 127;
		if (macid) {
			u32 tid = *(u32 *)(memory + RTL_AX_NI_TIDS + id * 4) & 7;
			*(u32 *)(memory + NI_TID_PENDING + (macid * 8 + tid) * 4) -= returned;
			ni_peer_event(mailbox, memory, macid);
		}
#endif
		if (buffer) break;
#ifdef PE_UPLINK
		if (!dropped && macid) {
			static const u8 ac[8] = {0, 1, 1, 0, 2, 2, 3, 3};
			u32 tid = *(u32 *)(memory + RTL_AX_NI_TIDS + id * 4) & 7;
			(*(u32 *)(memory + RTL_AX_TX_NI_AC_PENDING + (macid * 4 + ac[tid]) * 4))--;
		}
#endif
		owners[id * 2 + 1] = 0;
		released++;
		if (dropped) mailbox[47] = 0;
		else {
			pe_wp_release(memory, *(u32 *)(memory + RTL_AX_NI_COOKIES + id * 4));
			u32 *state = (void *)(memory + RTL_AX_TX_OWNER_STATE);
			((u16 *)(memory + RTL_AX_NI_FREE))[state[1] & (RTL_AX_NI_REQUESTS - 1)] = id;
			state[1]++;
			mailbox[46] = (consumer + 1) & RTL_AX_NI_COMPLETION_MASK;
		}
	}
	/* PE1 alone selects/recycles WPs; flush totals before selection resumes. */
	if (recycled) mailbox[29] += recycled;
	if (released) mailbox[30] -= released;
}

void __attribute__((section(".text.main"))) pe_main(volatile u32 *control)
{
	u32 slot = 1;
	u32 contexts[2] = {}, generations[2] = {}, commands[2] = {};
#ifdef PE_UPLINK
	u32 rx_poll[2] = {};
	/* PE1 alone owns selected requests; both radios fit in private DMEM. */
	u32 tx_requests[2][16][32];
#endif
#ifndef PE_UPLINK
	static const u32 indices[] = { 0x1058, 0x105c, 0x1060, 0x1064, 0x1078, 0x107c };
#endif
	u32 cp0;

	__asm__ volatile("mfc0 %0, $15" : "=r"(cp0)); control[48] = cp0;
	__asm__ volatile("mfc0 %0, $12" : "=r"(cp0)); control[49] = cp0;
	__asm__ volatile("mfc0 %0, $16" : "=r"(cp0)); control[50] = cp0;
	__asm__ volatile("mfc0 %0, $16, 1" : "=r"(cp0)); control[51] = cp0;
	control[60] = *(volatile u32 *)0xb0020210;

	control[0] = 0x50455744;
	for (;;) {
		slot ^= 1;
		u32 generation = control[142 + slot];
		/* Only a generation change publishes or withdraws a context. */
		if (generation != generations[slot]) {
			sync();
			contexts[slot] = control[140 + slot];
			generations[slot] = generation;
			commands[slot] = 0;
			control[144 + slot] = generation;
		}
		u32 context = contexts[slot];
		if (!context) continue;
		volatile u32 *mailbox = (void *)context;
		u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
		u32 consumer = mailbox[9];
		if (!mailbox[0]) {
			for (u32 i = 48; i < 62; i++) mailbox[i] = control[i];
			mailbox[60] = control[60];
			sync(); mailbox[0] = 0x50455744;
		}
		u32 command = mailbox[3];
#ifdef PE_UPLINK
		/* PE1 finishes its selected MPDUs before acknowledging a pause. */
		if (!mailbox[80] && !(command & 1) && command != commands[slot]) {
			pe_tx_publish(mailbox, 16, tx_requests[slot]);
			if (mailbox[RTL_AX_TX_PUBLISH_READ] != mailbox[RTL_AX_TX_PUBLISH_WRITE]) continue;
		}
#endif
		if (command != commands[slot]) {
			/* Complete the previous turn before acknowledging a pause. */
			sync();
			mailbox[4] = command;
			commands[slot] = command;
		}
		u32 ni_packet = 0;
#ifndef PE_UPLINK
		u32 ni_buffer = 0;
#endif
		u32 ni_frames = 1;
#ifdef PE_UPLINK
		u32 *ni_request;
#else
		u32 ni_request[32];
#endif
		#ifndef PE_UPLINK
		volatile u32 *ni = (void *)0xb0000000;
		volatile u32 *owners = (void *)(memory + RTL_AX_NI_OWNERS);
#endif
		if (!(command & 1)) {
			continue;
		}
#ifdef PE_UPLINK
		if (mailbox[80]) {
			/* PE0 owns capture, ordering, NI submission and payload recycling. */
			if (mailbox[RTL_AX_RX_WORK_READ] != mailbox[RTL_AX_RX_WORK_WRITE] ||
			    ((*(volatile u32 *)uncached(rx_buffer_dma(mailbox,
			        *(u32 *)(memory + RTL_AX_RX_SLOT_MAP + mailbox[81] * 4))) >> 16) & 8191) ==
			        (mailbox[82] == 8191 ? 1 : mailbox[82] + 1) || !(++rx_poll[slot] & 15))
				pe_rxq_batch(mailbox);
			pe_rx_work_batch(mailbox);
			continue;
		}
		/* PE1 alone writes the shared timer; PE0 only posts RX events. */
		if (mailbox[RTL_AX_RX_EVENT_CONTEXT])
			pe_event_flush(uncached(mailbox[RTL_AX_RX_EVENT_CONTEXT]));
#endif
		pe_rpq(mailbox, memory);
#ifndef PE_UPLINK
		for (u32 budget = 0; budget < 16; budget++) {
			u32 before = mailbox[81] | mailbox[RTL_AX_RX_WORK_PROCESS] << 8;
			pe_rxq(mailbox);
			if ((mailbox[81] | mailbox[RTL_AX_RX_WORK_PROCESS] << 8) == before) break;
		}
#endif
		pe_event_flush(mailbox);
#ifdef PE_UPLINK
		if (mailbox[24]) ni_ps_scan(mailbox, memory);
#endif
		if (mailbox[30]) ni_recycle(mailbox, memory);
		u32 arm_producer = mailbox[8];
#ifdef PE_UPLINK
		/* An idle non-offloaded radio still services reports and events,
		 * but has no NI queues or selected requests to build/publish. */
		if (!mailbox[24] && consumer == arm_producer &&
		    mailbox[RTL_AX_TX_PUBLISH_READ] == mailbox[RTL_AX_TX_PUBLISH_WRITE]) continue;
#endif
		/* Acquire only the sampled ARM prefix; later arrivals wait for the
		 * next turn's barrier instead of entering this batch unsynchronized. */
		sync();
		/* Stock phl_handle_pending_wd publishes a completed TXBD batch.
		 * Bound each radio's turn so RPQ, events and the other radio still run. */
#ifndef PE_UPLINK
		u32 publish = 0, producers[6];
#endif
#ifdef PE_UPLINK
		for (u32 burst = 0; burst < TX_BURST; burst++)
#endif
		{
#ifdef PE_UPLINK
			if (((mailbox[RTL_AX_TX_PUBLISH_WRITE] + 1) & 15) == mailbox[RTL_AX_TX_PUBLISH_READ]) goto publish_tx;
			/* PE1 is both producer and consumer: build NI requests directly
			 * in the reserved slot while retaining the publish barrier. */
			ni_request = tx_requests[slot][mailbox[RTL_AX_TX_PUBLISH_WRITE]];
#endif
			ni_packet = 0; ni_frames = 1;
			const u32 *request = (void *)(memory + REQUEST_BASE + consumer * 128);
			if (consumer == arm_producer) {
				if (!mailbox[24]) goto publish_tx;
#ifdef PE_UPLINK
				/* Share admission work across this bounded publish turn;
				 * refill early if selections consume the queued input. */
				if (!burst || mailbox[38] < 8)
					ni_ingest(mailbox, memory);
				ni_frames = ni_select(mailbox, memory, ni_request);
				if (!ni_frames) goto publish_tx;
#else
				u32 queued = ((ni[0x7060 / 4] - mailbox[26]) & 1023) / 8;
				mailbox[38] = queued;
				volatile u32 *ps = (void *)(memory + 0xe800 + mailbox[27] * 16);
				ni_buffered_event(mailbox, queued && ps[0] && mailbox[62] != mailbox[27]);
				if (!queued || !ni_owner_available(memory) || mailbox[47]) continue;
				u32 asleep = ps[0], grant = ps[1];
				u32 retired = mailbox[62] == mailbox[27];
				if (!asleep) ps[2] = grant;
				if (!retired && asleep && grant == ps[2]) continue;
				sync();
				u32 id = RTL_AX_NI_DISCARD;
				const u32 *entry = uncached(mailbox[25] + mailbox[26]);
				ni_buffer = entry[0] & 0xffffff80;
				u32 header, length = ni_length(entry, ni_buffer, &header, 0);
				/* One EPP owns one 2 KiB BID; never gather past that DMA allocation. */
				if (retired || (entry[0] & 0x50) != 0x50 || length < 14 || length > RTL_QM_DATA_BYTES - header) {
					owners[id * 2] = ni_buffer;
					owners[id * 2 + 1] = BIT(31);
					*(u32 *)uncached(ni_buffer) = 0;
					mailbox[47] = id + 1;
					mailbox[30]++;
					if (retired) mailbox[63]++;
					else mailbox[40]++;
					mailbox[26] = (mailbox[26] + 8) & 1023;
					sync(); ni[0x7160 / 4] = mailbox[26];
					if (mailbox[78]) pe_event(mailbox);
					continue;
				}
				/* Reserve the tag before consuming an EPP in the direct path. */
				if (!pe_wp_available(memory, 0)) continue;
				id = ni_owner(memory);
				const u32 *peer = (void *)(memory + 0xd000 + mailbox[27] * 32);
				for (u32 i = 0; i < 8; i++) ni_request[i] = peer[i];
				ni_request[4] &= ~0x1fffU;
				if (asleep) {
					ni_request[4] |= (queued > 1 || (ps[3] & 2) ? BIT(19) : 0) |
						(ps[3] & 1 ? BIT(20) : 0);
					ps[2] = grant;
					*(u32 *)(memory + RTL_AX_NI_GENERATIONS + id * 4) = grant;
					mailbox[39]++;
				}
				ni_request[0] = 256 + id;
				ni_request[1] = 0;
				u32 caps = *(u32 *)(memory + 0xe200 + mailbox[27] * 4);
				if (caps & BIT(17)) ni_request[3] |= BIT(20);
				u32 probe = (mailbox[56] >> 5) & 15;
				if (!(caps & BIT(17)) && !asleep && !(mailbox[56] & 31) && (caps & BIT(probe))) {
					ni_request[3] = 0x80 | probe | (caps & BIT(16) ? BIT(12) : 0) | BIT(19);
				}
				ni_request[2] = (ni_request[2] & 0x3f0000) | BIT(24) |
					(length + ((ni_request[2] >> 16) & 63) + 8 - 14);
				ni_request[8] = ni_buffer + RTL_QM_HEADROOM + header;
				ni_request[9] = length;
				owners[id * 2] = ni_buffer;
				owners[id * 2 + 1] = (asleep ? BIT(16) : 0) | mailbox[27] << 8 |
					((ni_request[5] >> 16) & 127) << 17 | BIT(15);
				*(u32 *)uncached(ni_buffer) = 0;
				if (!asleep && queued > 1 &&
				    ((ni_request[2] >> 16) & 63) == 26 && !(ni_request[3] & BIT(19))) {
					const u32 *second = uncached(mailbox[25] + ((mailbox[26] + 8) & 1023));
					u32 buffer2 = second[0] & 0xffffff80, head2;
					u32 bytes2 = ni_length(second, buffer2, &head2, 0);
					u32 pad = (0U - length - 8) & 3;
					if ((second[0] & 0x50) == 0x50 && bytes2 >= 14 && bytes2 <= RTL_QM_DATA_BYTES - head2 &&
					    length + bytes2 + 16 + pad <= (caps >> 18)) {
						ni_request[4] |= BIT(21);
						ni_request[2] = BIT(25) | 26U << 16 | (26 + length + bytes2 + 16 + pad);
						ni_request[10] = buffer2 + RTL_QM_HEADROOM + head2;
						ni_request[11] = bytes2;
						*(u32 *)uncached(ni_buffer) = buffer2;
						*(u32 *)uncached(buffer2) = 0;
						ni_frames = 2;
					}
				}
				mailbox[30]++;
				mailbox[26] = (mailbox[26] + ni_frames * 8) & 1023;
				mailbox[38] = queued - ni_frames;
				sync(); ni[0x7160 / 4] = mailbox[26];
#endif
				request = ni_request; ni_packet = 1;
			}
#ifdef PE_UPLINK
			{
				u32 write = mailbox[RTL_AX_TX_PUBLISH_WRITE];
				u32 words = 8 + (request[2] >> 24) * 2;
				u32 *out = tx_requests[slot][write];
				if (request != out)
					for (u32 i = 0; i < words; i++) out[i] = request[i];
				mailbox[RTL_AX_TX_PUBLISH_WRITE] = (write + 1) & 15;
			}
#else
			{
				u32 ring = request[1] & 7, next;
				if (!ni_packet && !pe_wp_available(memory, ring)) continue;
				pe_build_tx(mailbox, memory, request, &next);
				producers[ring] = next; publish |= BIT(ring);
			}
#endif
			if (!ni_packet) {
				consumer = (consumer + 1) & 255;
				mailbox[9] = consumer;
			}
		}
	publish_tx:
#ifdef PE_UPLINK
		pe_tx_publish(mailbox, TX_BURST, tx_requests[slot]);
#else
		if (publish) {
			sync();
#pragma clang loop unroll(disable)
			for (u32 ring = 0; ring < 6; ring++)
				if (publish & BIT(ring)) *(volatile u16 *)(mailbox[2] + indices[ring]) = producers[ring];
			sync();
		}
#endif
	}
}
