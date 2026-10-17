// SPDX-License-Identifier: GPL-2.0-only
/* Baseline MIB upload, derived from the original libponrelay builders/parsers. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <poll.h>
#include <string.h>
#include <time.h>

#include "omci-mib.h"
#include "omci-socket.h"

static uint16_t read_be16(const uint8_t *data)
{
	return (uint16_t)data[0] << 8 | data[1];
}

static void write_be16(uint8_t *data, uint16_t value)
{
	data[0] = value >> 8;
	data[1] = value;
}

void fttr_mib_request(uint16_t transaction, bool next, uint16_t sequence, uint8_t body[44])
{
	memset(body, 0, 44);
	write_be16(body, transaction);
	body[2] = next ? 0x4e : 0x4d;
	body[3] = 0x0a;
	write_be16(body + 4, 2);
	if (next)
		write_be16(body + 8, sequence);
	body[43] = 0x28;
}

void fttr_mib_record_decode(const uint8_t body[44], struct fttr_mib_record *record)
{
	record->class_id = read_be16(body + 8);
	record->instance = read_be16(body + 10);
	record->mask = read_be16(body + 12);
	memcpy(record->attributes, body + 14, sizeof(record->attributes));
}

static int64_t monotonic_ms(void)
{
	struct timespec now;

	clock_gettime(CLOCK_MONOTONIC, &now);
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int mib_exchange(struct fttr_omci_session *session, bool next,
			uint16_t sequence, uint8_t response[44])
{
	uint8_t request[44], frame[1996];
	struct fttr_omci_message message;
	struct pollfd pollfd = { .fd = session->fd, .events = POLLIN };
	int64_t now, deadline;
	unsigned int attempt;
	int ret;

	/* The original high-priority allocator uses 8001..ffff. */
	session->transaction = session->transaction == UINT16_MAX ? 0x8001 : session->transaction + 1;
	fttr_mib_request(session->transaction, next, sequence, request);
	for (attempt = 0; attempt < 3; attempt++) {
		ret = fttr_omci_send(session->fd, &session->link, session->onu, request, sizeof(request));
		if (ret < 0)
			return ret;
		now = monotonic_ms();
		deadline = now + 2000;
		for (;;) {
			now = monotonic_ms();
			if (now >= deadline)
				break;
			ret = poll(&pollfd, 1, deadline - now);
			if (ret < 0)
				return -errno;
			if (!ret)
				break;
			if (pollfd.revents & (POLLERR | POLLHUP | POLLNVAL))
				return -EIO;
			ret = fttr_omci_receive(session->fd, session->link.ethertype, frame,
					 sizeof(frame), &message);
			if (ret == -EAGAIN)
				continue;
			if (ret)
				return ret;
			if (message.onu != session->onu || read_be16(message.body) != session->transaction)
				continue;
			if (message.length != 44 || message.body[3] != 0x0a ||
			    message.body[2] != (next ? 0x2e : 0x2d) ||
			    read_be16(message.body + 4) != 2 || read_be16(message.body + 6))
				return -EPROTO;
			memcpy(response, message.body, 44);
			return 0;
		}
	}
	return -ETIMEDOUT;
}

int fttr_mib_scan(struct fttr_omci_session *session, uint16_t *commands,
		 int (*record)(void *context, uint16_t sequence, const struct fttr_mib_record *entry),
		 void *context)
{
	struct fttr_mib_record entry;
	uint8_t response[44];
	unsigned int sequence;
	int ret = mib_exchange(session, false, 0, response);

	if (ret)
		return ret;
	*commands = read_be16(response + 8);
	for (sequence = 0; sequence < *commands; sequence++) {
		ret = mib_exchange(session, true, sequence, response);
		if (ret)
			return ret;
		fttr_mib_record_decode(response, &entry);
		ret = record(context, sequence, &entry);
		if (ret)
			return ret;
	}
	return 0;
}
