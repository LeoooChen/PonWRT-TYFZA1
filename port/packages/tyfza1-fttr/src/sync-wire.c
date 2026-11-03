// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <string.h>

#include "sync-wire.h"

int fttr_sync_pack(const uint8_t source[6], const uint8_t destination[6],
		uint16_t ethertype, uint8_t slot, uint8_t type,
		const void *payload, size_t length, uint8_t *frame, size_t capacity)
{
	if (slot > 15 || ethertype < 0x600 ||
	    (type != FTTR_SYNC_REQUEST && type != FTTR_SYNC_RESPONSE))
		return -EINVAL;
	if (length > FTTR_SYNC_FRAME_MAX - FTTR_SYNC_HEADER ||
	    capacity < FTTR_SYNC_HEADER || length > capacity - FTTR_SYNC_HEADER)
		return -EMSGSIZE;
	memcpy(frame, destination, 6);
	memcpy(frame + 6, source, 6);
	frame[12] = ethertype >> 8;
	frame[13] = ethertype;
	frame[14] = slot << 4 | type;
	frame[15] = 0;
	frame[16] = length >> 24;
	frame[17] = length >> 16;
	frame[18] = length >> 8;
	frame[19] = length;
	if (length)
		memcpy(frame + FTTR_SYNC_HEADER, payload, length);
	return FTTR_SYNC_HEADER + length;
}

int fttr_sync_unpack(const uint8_t *frame, size_t length, uint16_t ethertype,
		    struct fttr_sync_message *message)
{
	uint32_t payload;

	memset(message, 0, sizeof(*message));
	if (length < FTTR_SYNC_HEADER || length > FTTR_SYNC_FRAME_MAX)
		return -EMSGSIZE;
	if (((uint16_t)frame[12] << 8 | frame[13]) != ethertype)
		return -EPROTO;
	if ((frame[14] & 15) != FTTR_SYNC_REQUEST && (frame[14] & 15) != FTTR_SYNC_RESPONSE)
		return -EOPNOTSUPP;
	/* Original receive path treats upper flags as fragmentation. No partial
	 * message may be exposed as a complete JSON response. */
	if (frame[15])
		return -EOPNOTSUPP;
	payload = (uint32_t)frame[16] << 24 | (uint32_t)frame[17] << 16 |
		(uint32_t)frame[18] << 8 | frame[19];
	if (payload > length - FTTR_SYNC_HEADER)
		return -EMSGSIZE;
	/* Ethernet minimum-frame padding may follow a short JSON message. */
	if (length > 60 && payload != length - FTTR_SYNC_HEADER)
		return -EBADMSG;
	memcpy(message->destination, frame, 6);
	memcpy(message->source, frame + 6, 6);
	message->slot = frame[14] >> 4;
	message->type = frame[14] & 15;
	message->payload = frame + FTTR_SYNC_HEADER;
	message->length = payload;
	return 0;
}
