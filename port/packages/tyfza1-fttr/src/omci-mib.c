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

static int job_fail(struct fttr_mib_job *job, int error)
{
	job->step = FTTR_MIB_FAILED;
	job->error = error;
	return error;
}

static void job_request(struct fttr_mib_job *job, uint16_t *counter)
{
	/* The original high-priority allocator uses 8001..ffff. */
	*counter = *counter == UINT16_MAX ? 0x8001 : *counter + 1;
	job->transaction = *counter;
	job->attempts = 0;
	fttr_mib_request(job->transaction, job->step == FTTR_MIB_NEXT, job->sequence, job->request);
}

void fttr_mib_job_start(struct fttr_mib_job *job, uint16_t onu, uint16_t *counter)
{
	memset(job, 0, sizeof(*job));
	job->onu = onu;
	job->step = FTTR_MIB_UPLOAD;
	job_request(job, counter);
}

int fttr_mib_job_send(struct fttr_mib_job *job, int fd, const struct fttr_omci_link *link, int64_t now)
{
	int ret = fttr_omci_send(fd, link, job->onu, job->request, sizeof(job->request));

	if (ret < 0)
		return job_fail(job, ret);
	job->attempts++;
	job->deadline = now + 2000;
	return 0;
}

int fttr_mib_job_timeout(struct fttr_mib_job *job, int fd, const struct fttr_omci_link *link, int64_t now)
{
	if ((job->step != FTTR_MIB_UPLOAD && job->step != FTTR_MIB_NEXT) || now < job->deadline)
		return 0;
	if (job->attempts == 3)
		return job_fail(job, -ETIMEDOUT);
	return fttr_mib_job_send(job, fd, link, now);
}

int fttr_mib_job_reply(struct fttr_mib_job *job, const struct fttr_omci_message *message,
		       uint16_t *counter, struct fttr_mib_record *record)
{
	int action = 0;
	bool next = job->step == FTTR_MIB_NEXT;

	if ((job->step != FTTR_MIB_UPLOAD && !next) || message->onu != job->onu ||
	    read_be16(message->body) != job->transaction)
		return 0;
	if (message->length != 44 || message->body[3] != 0x0a ||
	    message->body[2] != (next ? 0x2e : 0x2d) ||
	    read_be16(message->body + 4) != 2 || read_be16(message->body + 6))
		return job_fail(job, -EPROTO);
	if (next) {
		fttr_mib_record_decode(message->body, record);
		job->sequence++;
		action = FTTR_MIB_RECORD;
	} else {
		job->commands = read_be16(message->body + 8);
		job->step = FTTR_MIB_NEXT;
	}
	if (job->sequence == job->commands) {
		job->step = FTTR_MIB_DONE;
		return action | FTTR_MIB_COMPLETE;
	}
	job_request(job, counter);
	return action | FTTR_MIB_REQUEST;
}

int fttr_mib_scan(struct fttr_omci_session *session, uint16_t *commands,
		 int (*record)(void *context, uint16_t sequence, const struct fttr_mib_record *entry),
		 void *context)
{
	struct fttr_mib_job job;
	struct fttr_mib_record entry;
	struct fttr_omci_message message;
	struct pollfd pollfd = { .fd = session->fd, .events = POLLIN };
	uint8_t frame[1996];
	unsigned int sequence;
	int64_t now;
	int ret, action;

	fttr_mib_job_start(&job, session->onu, &session->transaction);
	ret = fttr_mib_job_send(&job, session->fd, &session->link, monotonic_ms());
	if (ret)
		return ret;
	for (;;) {
		now = monotonic_ms();
		ret = fttr_mib_job_timeout(&job, session->fd, &session->link, now);
		if (ret)
			return ret;
		ret = poll(&pollfd, 1, job.deadline - now);
		if (ret < 0)
			return -errno;
		if (!ret)
			continue;
		if (pollfd.revents & (POLLERR | POLLHUP | POLLNVAL))
			return -EIO;
		ret = fttr_omci_receive(session->fd, session->link.ethertype, frame, sizeof(frame), &message);
		if (ret == -EAGAIN)
			continue;
		if (ret)
			return ret;
		sequence = job.sequence;
		action = fttr_mib_job_reply(&job, &message, &session->transaction, &entry);
		if (action < 0)
			return action;
		*commands = job.commands;
		if (action & FTTR_MIB_RECORD) {
			ret = record(context, sequence, &entry);
			if (ret)
				return ret;
		}
		if (action & FTTR_MIB_COMPLETE)
			return 0;
		if (action & FTTR_MIB_REQUEST) {
			ret = fttr_mib_job_send(&job, session->fd, &session->link, monotonic_ms());
			if (ret)
				return ret;
		}
	}
}
