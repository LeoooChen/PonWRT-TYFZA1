/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_SYNC_WIRE_H
#define TYFZA1_FTTR_SYNC_WIRE_H

#include <stddef.h>
#include <stdint.h>

/* sync_controllerd is a separate Ethernet protocol from MiniOLT OMCI.
 * Its four-bit slot is a syncserv peer slot, not a hardware ONU ID. */
#define FTTR_SYNC_HEADER 20
#define FTTR_SYNC_FRAME_MAX 1996
#define FTTR_SYNC_REQUEST 6
#define FTTR_SYNC_RESPONSE 7
#define FTTR_SYNC_PART_MAX (FTTR_SYNC_FRAME_MAX - 16)
#define FTTR_SYNC_BODY_MAX (8 * FTTR_SYNC_PART_MAX)
#define FTTR_SYNC_REASSEMBLY_MS 3000

struct fttr_sync_message {
	uint8_t source[6], destination[6], slot, type;
	const uint8_t *payload;
	size_t length;
};

/* One bounded exchange/peer per context. Zero before starting a new query;
 * the two-bit sequence is not an authenticated transaction identifier. */
struct fttr_sync_receiver {
	uint8_t source[6], type, sequence, count, mask;
	uint16_t ethertype;
	int64_t deadline;
	size_t lengths[8];
	uint8_t parts[8][FTTR_SYNC_PART_MAX], body[FTTR_SYNC_BODY_MAX];
};

int fttr_sync_pack(const uint8_t source[6], const uint8_t destination[6],
		uint16_t ethertype, uint8_t slot, uint8_t type,
		const void *payload, size_t length, uint8_t *frame, size_t capacity);
int fttr_sync_unpack(const uint8_t *frame, size_t length, uint16_t ethertype,
		    struct fttr_sync_message *message);
/* -EAGAIN means incomplete/identical duplicate/unrelated owner. On success
 * payload remains valid until the next receive call or context reset. */
int fttr_sync_receive(struct fttr_sync_receiver *receiver, const uint8_t *frame,
		size_t length, uint16_t ethertype, int64_t now,
		struct fttr_sync_message *message);

#endif
