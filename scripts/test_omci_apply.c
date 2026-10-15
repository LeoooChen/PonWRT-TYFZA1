/* SPDX-License-Identifier: GPL-2.0-only */
/* Hardware rejection must leave the OLT response, MIB and replay cache consistent. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pon-omci.h"

struct backend {
	uint8_t *reply;
	unsigned int result_offset, calls, count;
	int fail;
};
static unsigned int transaction, extended, frame_length;
static uint8_t frame[128], reply[2048], first[2048];
static void be16(uint8_t *p, unsigned int n) { p[0] = n >> 8; p[1] = n; }
static unsigned int le16(const uint8_t *p) { return p[0] | p[1] << 8; }
static int apply(void *context, const uint8_t *paths, size_t length)
{
	struct backend *b = context;
	/* The backend runs before the successful reply is made visible to transport. */
	assert(b->reply[b->result_offset] == 0xa5);
	assert(length >= 12 && le16(paths) == 4);
	b->calls++;
	if (b->fail) return b->fail;
	b->count = le16(paths + 2);
	return 0;
}
static int dispatch(struct omci_agent *agent, struct backend *b, int *error)
{
	memset(reply, 0xa5, sizeof(reply));
#ifdef OMCI_OLD_ORDER
	int length = tyfza1_omci_dispatch(agent, frame, frame_length, reply, 0);
	uint8_t paths[65536];
	int bytes = tyfza1_omci_paths(agent, paths);
	if (bytes > 0) *error = apply(b, paths, bytes);
	return length;
#else
	return tyfza1_omci_dispatch_apply(agent, frame, frame_length, reply, error, apply, b);
#endif
}
static unsigned int request(struct omci_agent *agent, struct backend *b,
	unsigned int action, unsigned int cls, unsigned int id, const uint8_t *body, unsigned int bytes)
{
	memset(frame, 0, sizeof(frame));
	be16(frame, ++transaction); frame[2] = 0x40 | action; frame[3] = extended ? 11 : 10;
	be16(frame + 4, cls); be16(frame + 6, id);
	unsigned int offset = extended ? 10 : 8;
	frame_length = extended ? 10 + bytes : 44;
	if (extended) be16(frame + 8, bytes); else frame[43] = 0x28;
	memcpy(frame + offset, body, bytes);
	int error = 0;
	assert(dispatch(agent, b, &error) > 0);
	return reply[offset];
}
int main(void)
{
	for (extended = 0; extended < 2; extended++) {
		struct omci_identity identity = {.serial = {'T','E','S','T',1,2,3,4}};
		struct omci_agent *a = tyfza1_omci_new(&identity);
		struct backend b = {.reply = reply, .result_offset = extended ? 10 : 8};
		uint8_t body[32] = {}, paths[65536], bridge[14];
		body[0] = 0x80; be16(body + 2, 1063);
		assert(!request(a, &b, 8, 262, 0x8000, body, 4));
		memset(body, 0, sizeof(body)); be16(body, 4000); be16(body + 2, 0x8000);
		body[4] = 3; be16(body + 5, 0x8007); be16(body + 7, 0xffff);
		be16(body + 9, 0xffff); be16(body + 11, 0xffff);
		assert(!request(a, &b, 4, 268, 0x100, body, 13));
		memset(body, 0, sizeof(body)); be16(body, 0x100); body[2] = 5;
		be16(body + 3, 0xffff); be16(body + 5, 0xffff);
		assert(!request(a, &b, 4, 266, 0x200, body, 10));
		memset(body, 0, sizeof(body)); be16(body, 1); body[2] = 1; body[3] = 5; be16(body + 4, 0x200);
		memcpy(bridge, body, sizeof(bridge));
		b.fail = -EIO;
		assert(request(a, &b, 4, 47, 0x300, body, 14) == 1);
		assert(tyfza1_omci_paths(a, paths) == 12 && b.count == 0);
		/* Identical retransmission executes again after failure, then caches the ACK. */
		b.fail = 0; int error = 0;
		int bytes = dispatch(a, &b, &error);
		assert(bytes > 0 && !reply[b.result_offset] && !error && b.count == 1);
		memcpy(first, reply, bytes); unsigned int calls = b.calls;
		assert(dispatch(a, &b, &error) == bytes && !memcmp(first, reply, bytes) && b.calls == calls);
		assert(tyfza1_omci_paths(a, paths) == 62 && le16(paths + 14) == 4000);
		b.fail = -ETIMEDOUT;
		assert(request(a, &b, 6, 47, 0x300, body, 0) == 1);
		assert(tyfza1_omci_paths(a, paths) == 62 && b.count == 1);
		b.fail = 0; error = 0;
		assert(dispatch(a, &b, &error) > 0 && !reply[b.result_offset] && !error && b.count == 0);
		assert(!request(a, &b, 4, 47, 0x300, bridge, sizeof(bridge)) && b.count == 1);
		/* A failed SET cannot alter the existing T-CONT, or its MIB Data Sync. */
		body[0] = 0x80; body[1] = 0;
		assert(!request(a, &b, 9, 2, 0, body, 2));
		unsigned int value = b.result_offset + (extended ? 7 : 3);
		unsigned int sync = reply[value];
		be16(body + 2, 2000); b.fail = -EIO;
		assert(request(a, &b, 8, 262, 0x8000, body, 4) == 1);
		assert(!request(a, &b, 9, 2, 0, body, 2) && reply[value] == sync);
		assert(!request(a, &b, 9, 262, 0x8000, body, 2));
		assert(reply[value] == (1063 >> 8) && reply[value + 1] == (1063 & 255));
		assert(request(a, &b, 15, 2, 0, body, 0) == 1);
		assert(!request(a, &b, 9, 262, 0x8000, body, 2) && reply[value] == (1063 >> 8));
		b.fail = 0;
		assert(!request(a, &b, 15, 2, 0, body, 0));
		assert(!request(a, &b, 9, 262, 0x8000, body, 2) && reply[value] == 255 && reply[value + 1] == 255);
		tyfza1_omci_free(a);
	}
	puts("Baseline/extended OMCI: hardware apply precedes ACK; failed Create/Delete/Set preserve MIB/sync and remain retryable; committed retransmissions skip backend");
}
