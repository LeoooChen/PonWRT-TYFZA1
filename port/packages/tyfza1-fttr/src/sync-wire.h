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

struct fttr_sync_message {
	uint8_t source[6], destination[6], slot, type;
	const uint8_t *payload;
	size_t length;
};

int fttr_sync_pack(const uint8_t source[6], const uint8_t destination[6],
		uint16_t ethertype, uint8_t slot, uint8_t type,
		const void *payload, size_t length, uint8_t *frame, size_t capacity);
int fttr_sync_unpack(const uint8_t *frame, size_t length, uint16_t ethertype,
		    struct fttr_sync_message *message);

#endif
