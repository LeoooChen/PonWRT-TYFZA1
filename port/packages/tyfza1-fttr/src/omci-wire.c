// SPDX-License-Identifier: GPL-2.0-only
/* Native MiniOLT Ethernet/OMCI framing, derived from libponrelay.so. */
#include <errno.h>
#include <string.h>

#include "omci-wire.h"

static uint16_t read_be16(const uint8_t *data)
{
	return (uint16_t)data[0] << 8 | data[1];
}

static void write_be16(uint8_t *data, uint16_t value)
{
	data[0] = value >> 8;
	data[1] = value;
}

static uint32_t read_be32(const uint8_t *data)
{
	return (uint32_t)data[0] << 24 | (uint32_t)data[1] << 16 |
	       (uint32_t)data[2] << 8 | data[3];
}

static void write_be32(uint8_t *data, uint32_t value)
{
	data[0] = value >> 24;
	data[1] = value >> 16;
	data[2] = value >> 8;
	data[3] = value;
}

uint32_t fttr_omci_crc(const uint8_t *data, size_t length)
{
	uint32_t crc = UINT32_MAX;
	size_t i;
	unsigned int bit;

	for (i = 0; i < length; i++) {
		crc ^= (uint32_t)data[i] << 24;
		for (bit = 0; bit < 8; bit++)
			crc = (crc << 1) ^ (crc & 0x80000000U ? 0x04c11db7U : 0);
	}
	return ~crc;
}

static int omci_body_length(const uint8_t *body, size_t available)
{
	unsigned int length;

	if (available < 4)
		return -EMSGSIZE;
	switch (body[3]) {
	case 0x0a:
		length = 44;
		break;
	case 0x0b:
		if (available < 10)
			return -EMSGSIZE;
		length = 10 + read_be16(body + 8);
		break;
	default:
		return -EOPNOTSUPP;
	}
	return length > available ? -EMSGSIZE : (int)length;
}

int fttr_omci_pack(const struct fttr_omci_link *link, uint16_t onu,
		   const uint8_t *body, size_t length, uint8_t *frame, size_t capacity)
{
	size_t offset = link->downstream_length ? 18 : 16;
	size_t size = offset + length + 4 + (link->downstream_length ? 4 : 0);
	int ret = omci_body_length(body, length);

	if (ret < 0)
		return ret;
	if ((size_t)ret != length)
		return -EBADMSG;
	if (size > capacity || (link->downstream_length && length > UINT16_MAX - 4U))
		return -EMSGSIZE;
	memcpy(frame, link->olt_mac, 6);
	memcpy(frame + 6, link->host_mac, 6);
	write_be16(frame + 12, link->ethertype);
	write_be16(frame + 14, onu);
	if (link->downstream_length)
		write_be16(frame + 16, length + 4);
	memcpy(frame + offset, body, length);
	write_be32(frame + offset + length, fttr_omci_crc(body, length));
	if (link->downstream_length)
		memcpy(frame + offset + length + 4, "\0\1\2\3", 4);
	return size;
}

int fttr_omci_unpack(const uint8_t *frame, size_t length, uint16_t ethertype,
		     struct fttr_omci_message *message)
{
	const uint8_t *body;
	int size;

	if (length < 20)
		return -EMSGSIZE;
	if (read_be16(frame + 12) != ethertype)
		return -EPROTO;
	body = frame + 16;
	size = omci_body_length(body, length - 20);
	if (size < 0)
		return size;
	/* Preserve the original upstream software-CRC policy for ONU ID zero. */
	if (!read_be16(frame + 14) &&
	    read_be32(body + size) != fttr_omci_crc(body, size))
		return -EBADMSG;
	message->onu = read_be16(frame + 14);
	message->body = body;
	message->length = size;
	return 0;
}
