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
	/* Low two bits are a sequence, including for single-frame messages. */
	if (frame[15] & 0xfc)
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

int fttr_sync_receive(struct fttr_sync_receiver *receiver, const uint8_t *frame,
		size_t length, uint16_t ethertype, int64_t now,
		struct fttr_sync_message *message)
{
	uint8_t count, index, sequence, type;
	size_t part, total = 0, payload;
	unsigned int i;
	int ret = -EBADMSG;

	memset(message, 0, sizeof(*message));
	if (length < 17 || length > FTTR_SYNC_FRAME_MAX)
		return -EMSGSIZE;
	if (((uint16_t)frame[12] << 8 | frame[13]) != ethertype)
		return -EPROTO;
	type = frame[14] & 15;
	if (type != FTTR_SYNC_REQUEST && type != FTTR_SYNC_RESPONSE)
		return -EOPNOTSUPP;
	if (receiver->count && (memcmp(receiver->source, frame + 6, 6) ||
	    receiver->type != type || receiver->ethertype != ethertype))
		return -EAGAIN;
	if (receiver->count && now >= receiver->deadline) {
		ret = -ETIMEDOUT;
		goto reset;
	}
	count = (frame[15] >> 5) + 1;
	index = (frame[15] >> 2) & 7;
	sequence = frame[15] & 3;
	if (index >= count)
		goto reset;
	if (count == 1) {
		ret = fttr_sync_unpack(frame, length, ethertype, message);
		goto reset;
	}
	if (receiver->count && receiver->sequence != sequence)
		memset(receiver, 0, sizeof(*receiver));
	if (!receiver->count) {
		memcpy(receiver->source, frame + 6, 6);
		receiver->type = type;
		receiver->ethertype = ethertype;
		receiver->sequence = sequence;
		receiver->count = count;
		receiver->deadline = now + FTTR_SYNC_REASSEMBLY_MS;
	}
	if (receiver->count != count)
		goto reset;
	part = length - 16;
	if (receiver->mask & (1u << index)) {
		if (receiver->lengths[index] == part && !memcmp(receiver->parts[index], frame + 16, part))
			return -EAGAIN;
		goto reset;
	}
	memcpy(receiver->parts[index], frame + 16, part);
	receiver->lengths[index] = part;
	receiver->mask |= 1u << index;
	if (receiver->mask != (1u << count) - 1)
		return -EAGAIN;
	for (i = 0; i < count; i++) {
		memcpy(receiver->body + total, receiver->parts[i], receiver->lengths[i]);
		total += receiver->lengths[i];
	}
	if (total < 4)
		goto reset;
	payload = (uint32_t)receiver->body[0] << 24 | (uint32_t)receiver->body[1] << 16 |
		(uint32_t)receiver->body[2] << 8 | receiver->body[3];
	if (payload > total - 4) {
		ret = -EMSGSIZE;
		goto reset;
	}
	/* Only the terminal fragment can carry Ethernet minimum padding. Never
	 * discard bytes belonging to an earlier fragment or a larger frame. */
	if (payload != total - 4 && (receiver->lengths[count - 1] > 44 ||
	    total - 4 - payload >= receiver->lengths[count - 1]))
		goto reset;
	memcpy(message->source, receiver->source, 6);
	memcpy(message->destination, frame, 6);
	message->slot = frame[14] >> 4;
	message->type = type;
	message->payload = receiver->body + 4;
	message->length = payload;
	receiver->count = receiver->mask = 0;
	return 0;
reset:
	memset(receiver, 0, sizeof(*receiver));
	return ret;
}
