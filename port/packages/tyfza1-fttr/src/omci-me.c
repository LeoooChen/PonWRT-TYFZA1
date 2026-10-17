// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <poll.h>
#include <string.h>
#include <time.h>

#include "omci-me.h"
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

static int fail(struct fttr_me_job *job, int error)
{
	job->state = FTTR_ME_FAILED;
	job->error = error;
	return error;
}

int fttr_me_job_start(struct fttr_me_job *job, uint16_t onu, uint16_t *counter,
		      enum fttr_me_operation operation, uint16_t class_id, uint16_t instance,
		      uint16_t mask, const uint8_t *values, size_t size)
{
	size_t limit = operation == FTTR_ME_CREATE ? 32 : operation == FTTR_ME_SET ? 30 : 0;
	size_t offset = operation == FTTR_ME_SET ? 10 : 8;

	memset(job, 0, sizeof(*job));
	if (size > limit)
		return fail(job, -EMSGSIZE);
	*counter = *counter == UINT16_MAX ? 0x8001 : *counter + 1;
	job->transaction = *counter;
	job->onu = onu;
	job->mask = mask;
	write_be16(job->request, job->transaction);
	job->request[2] = operation;
	job->request[3] = 0x0a;
	write_be16(job->request + 4, class_id);
	write_be16(job->request + 6, instance);
	if (operation == FTTR_ME_GET || operation == FTTR_ME_SET)
		write_be16(job->request + 8, mask);
	if (size)
		memcpy(job->request + offset, values, size);
	job->request[43] = 0x28;
	return 0;
}

int fttr_me_job_send(struct fttr_me_job *job, int fd, const struct fttr_omci_link *link, int64_t now)
{
	int ret = fttr_omci_send(fd, link, job->onu, job->request, sizeof(job->request));

	if (ret < 0)
		return fail(job, ret);
	job->attempts++;
	job->deadline = now + 2000;
	return 0;
}

int fttr_me_job_timeout(struct fttr_me_job *job, int fd, const struct fttr_omci_link *link, int64_t now)
{
	if (job->state != FTTR_ME_WAIT || now < job->deadline)
		return 0;
	if (job->attempts == 3)
		return fail(job, -ETIMEDOUT);
	return fttr_me_job_send(job, fd, link, now);
}

int fttr_me_job_reply(struct fttr_me_job *job, const struct fttr_omci_message *message)
{
	const uint8_t *body = message->body;

	if (job->state != FTTR_ME_WAIT || message->onu != job->onu || read_be16(body) != job->transaction)
		return 0;
	if (message->length != 44 || body[3] != 0x0a || body[2] != ((job->request[2] & 0x1f) | 0x20) ||
	    memcmp(body + 4, job->request + 4, 4))
		return fail(job, -EPROTO);
	memcpy(job->reply, body, sizeof(job->reply));
	job->replied = true;
	job->result = body[8];
	if (job->result)
		return fail(job, -EREMOTEIO);
	if (job->request[2] == FTTR_ME_GET && read_be16(body + 9) != job->mask)
		return fail(job, -EPROTO);
	job->state = FTTR_ME_DONE;
	return 1;
}

static int64_t monotonic_ms(void)
{
	struct timespec now;

	clock_gettime(CLOCK_MONOTONIC, &now);
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

int fttr_me_exchange(struct fttr_omci_session *session, struct fttr_me_job *job)
{
	struct pollfd descriptor = { .fd = session->fd, .events = POLLIN };
	struct fttr_omci_message message;
	uint8_t frame[1996];
	int64_t now;
	int ret = fttr_me_job_send(job, session->fd, &session->link, monotonic_ms());

	if (ret)
		return ret;
	for (;;) {
		now = monotonic_ms();
		ret = fttr_me_job_timeout(job, session->fd, &session->link, now);
		if (ret)
			return ret;
		ret = poll(&descriptor, 1, job->deadline - now);
		if (ret < 0)
			return fail(job, -errno);
		if (!ret)
			continue;
		if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL))
			return fail(job, -EIO);
		ret = fttr_omci_receive(session->fd, session->link.ethertype, frame, sizeof(frame), &message);
		if (ret == -EAGAIN)
			continue;
		if (ret)
			return fail(job, ret);
		ret = fttr_me_job_reply(job, &message);
		if (ret)
			return ret < 0 ? ret : 0;
	}
}
