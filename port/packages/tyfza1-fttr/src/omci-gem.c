// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <poll.h>
#include <string.h>
#include <time.h>

#include "omci-gem.h"
#include "omci-socket.h"

/* Original peer result table at 169db0: code5 is unknown ME instance. */
#define OMCI_UNKNOWN_INSTANCE 5

static void be16(uint8_t *p, uint16_t value)
{
	p[0] = value >> 8;
	p[1] = value;
}

static void be32(uint8_t *p, uint32_t value)
{
	p[0] = value >> 24;
	p[1] = value >> 16;
	p[2] = value >> 8;
	p[3] = value;
}

static int request(struct fttr_gem_job *job, uint16_t *counter, enum fttr_me_operation operation)
{
	struct fttr_gem_object *object = &job->objects[job->index];

	/* All three original layouts fit the baseline Create/Get budgets. */
	fttr_me_job_start(&job->me, job->me.onu, counter, operation, object->class_id,
			 object->instance, object->mask,
			 operation == FTTR_ME_CREATE ? object->value : NULL,
			 operation == FTTR_ME_CREATE ? object->size : 0);
	return FTTR_GEM_REQUEST;
}

static int next_create(struct fttr_gem_job *job, uint16_t *counter)
{
	while (job->index < job->count) {
		if (job->objects[job->index].presence == FTTR_GEM_ABSENT) {
			job->phase = FTTR_GEM_CREATE;
			return request(job, counter, FTTR_ME_CREATE);
		}
		job->index++;
	}
	job->phase = FTTR_GEM_CONFIGURED;
	return FTTR_GEM_COMPLETE;
}

static int next_cleanup(struct fttr_gem_job *job, uint16_t *counter)
{
	while (job->index >= 0) {
		struct fttr_gem_object *object = &job->objects[job->index];

		if (object->touched) {
			bool unknown = object->presence == FTTR_GEM_UNKNOWN;

			job->phase = unknown ? FTTR_GEM_CLEANUP_PROBE : FTTR_GEM_DELETE;
			return request(job, counter, unknown ? FTTR_ME_GET : FTTR_ME_DELETE);
		}
		job->index--;
	}
	job->phase = job->error ? FTTR_GEM_FAILED : FTTR_GEM_RELEASED;
	return job->error ? job->error : FTTR_GEM_COMPLETE;
}

static int failure(struct fttr_gem_job *job, uint16_t *counter, int error)
{
	job->error = error;
	job->failure = job->me;
	job->index = job->count - 1;
	return next_cleanup(job, counter);
}

static int cleanup_failure(struct fttr_gem_job *job, int error)
{
	job->cleanup_error = error;
	job->cleanup_failure = job->me;
	job->phase = FTTR_GEM_FAILED;
	return job->error ? job->error : error;
}

void fttr_gem_job_start(struct fttr_gem_job *job, uint16_t onu, uint16_t *counter,
			const struct fttr_gem_config *config, const uint8_t serial[8])
{
	struct fttr_gem_object *object;

	memset(job, 0, sizeof(*job));
	job->me.onu = onu;
	memcpy(job->serial, serial, sizeof(job->serial));
	if (config->descriptor) {
		object = &job->objects[job->count++];
		object->class_id = 280;
		object->instance = config->descriptor;
		object->mask = 0xff00;
		object->size = 20;
		be32(object->value, config->committed);
		be32(object->value + 4, config->peak);
	}
	object = &job->objects[job->count++];
	object->class_id = 268;
	object->instance = config->entity;
	object->mask = 0xfac0;
	object->size = 14;
	be16(object->value, config->entity);
	be16(object->value + 2, config->tcont);
	object->value[4] = 3;
	be16(object->value + 5, config->queue);
	be16(object->value + 7, config->descriptor);
	object = &job->objects[job->count++];
	object->class_id = 266;
	object->instance = config->entity;
	object->mask = 0xf200;
	object->size = 9;
	be16(object->value, config->entity);
	object->value[2] = config->bridge ? 1 : 5;
	be16(object->value + 3, config->profile);
	be16(object->value + 7, config->gal);
	request(job, counter, FTTR_ME_GET);
}

int fttr_gem_job_update(struct fttr_gem_job *job, uint16_t *counter)
{
	struct fttr_gem_object *object;
	int error = job->me.state == FTTR_ME_FAILED ? job->me.error : 0;
	bool absent = error == -EREMOTEIO && job->me.result == OMCI_UNKNOWN_INSTANCE;

	if (job->phase == FTTR_GEM_CONFIGURED || job->phase == FTTR_GEM_RELEASED)
		return FTTR_GEM_COMPLETE;
	if (job->phase == FTTR_GEM_FAILED)
		return job->error ? job->error : job->cleanup_error;
	object = &job->objects[job->index];
	if (job->phase == FTTR_GEM_CREATE && job->me.attempts) {
		object->touched = true;
		object->presence = FTTR_GEM_UNKNOWN;
	}
	if (job->me.state == FTTR_ME_WAIT)
		return 0;
	switch (job->phase) {
	case FTTR_GEM_CHECK:
		if (absent) {
			object->presence = FTTR_GEM_ABSENT;
		} else {
			if (error)
				return failure(job, counter, error);
			object->presence = FTTR_GEM_PRESENT;
			if (memcmp(job->me.reply + 11, object->value, object->size))
				return failure(job, counter, -EEXIST);
			object->presence = FTTR_GEM_VERIFIED;
		}
		if (++job->index < job->count)
			return request(job, counter, FTTR_ME_GET);
		/* A reused dependent cannot own a newly created, missing prerequisite. */
		for (job->index = 1; job->index < job->count; job->index++)
			if (job->objects[job->index].presence == FTTR_GEM_VERIFIED &&
			    job->objects[job->index - 1].presence == FTTR_GEM_ABSENT)
				return failure(job, counter, -EUCLEAN);
		job->index = 0;
		return next_create(job, counter);
	case FTTR_GEM_CREATE:
		if (error)
			return failure(job, counter, error);
		object->presence = FTTR_GEM_PRESENT;
		job->phase = FTTR_GEM_VERIFY;
		return request(job, counter, FTTR_ME_GET);
	case FTTR_GEM_VERIFY:
		if (error)
			return failure(job, counter, error);
		object->presence = FTTR_GEM_PRESENT;
		if (memcmp(job->me.reply + 11, object->value, object->size))
			return failure(job, counter, -EUCLEAN);
		object->presence = FTTR_GEM_VERIFIED;
		job->index++;
		return next_create(job, counter);
	case FTTR_GEM_CLEANUP_PROBE:
	case FTTR_GEM_DELETE_VERIFY:
		if (absent) {
			object->presence = FTTR_GEM_ABSENT;
			object->touched = false;
			job->index--;
			return next_cleanup(job, counter);
		}
		if (error)
			return cleanup_failure(job, error);
		object->presence = FTTR_GEM_PRESENT;
		if (job->phase == FTTR_GEM_DELETE_VERIFY)
			return cleanup_failure(job, -EUCLEAN);
		job->phase = FTTR_GEM_DELETE;
		return request(job, counter, FTTR_ME_DELETE);
	case FTTR_GEM_DELETE:
		object->presence = FTTR_GEM_UNKNOWN;
		if (error && !absent)
			return cleanup_failure(job, error);
		job->phase = FTTR_GEM_DELETE_VERIFY;
		return request(job, counter, FTTR_ME_GET);
	default:
		return 0;
	}
}

int fttr_gem_job_release(struct fttr_gem_job *job, uint16_t *counter)
{
	job->index = job->count - 1;
	return next_cleanup(job, counter);
}

static void dump_error(FILE *file, const char *name, int error, const struct fttr_me_job *me)
{
	unsigned int i;

	fprintf(file, ",\"%s\":{\"error\":%d", name, error);
	if (error) {
		fprintf(file, ",\"class\":%u,\"instance\":%u,\"reply\":",
			(uint16_t)me->request[4] << 8 | me->request[5],
			(uint16_t)me->request[6] << 8 | me->request[7]);
		if (me->replied) {
			fprintf(file, "\"%02x", me->reply[0]);
			for (i = 1; i < sizeof(me->reply); i++)
				fprintf(file, "%02x", me->reply[i]);
			fputc('"', file);
		} else {
			fputs("null", file);
		}
	}
	fputc('}', file);
}

int fttr_gem_job_dump(const struct fttr_gem_job *job, FILE *file)
{
	static const char * const phases[] = { "check", "create", "verify", "cleanup-probe",
		"delete", "delete-verify", "configured", "released", "failed" };
	static const char * const states[] = { "unknown", "absent", "present", "verified" };
	int i;

	fprintf(file, "{\"event\":\"gem_state\",\"id\":%u,\"serial\":\"", job->me.onu);
	for (i = 0; i < 8; i++)
		fprintf(file, "%02x", job->serial[i]);
	fprintf(file, "\",\"phase\":\"%s\",\"objects\":[", phases[job->phase]);
	for (i = 0; i < job->count; i++) {
		const struct fttr_gem_object *object = &job->objects[i];

		fprintf(file, "%s{\"class\":%u,\"instance\":%u,\"state\":\"%s\",\"touched\":%s}",
			i ? "," : "", object->class_id, object->instance, states[object->presence],
			object->touched ? "true" : "false");
	}
	fputc(']', file);
	dump_error(file, "failure", job->error, &job->failure);
	dump_error(file, "cleanup_failure", job->cleanup_error, &job->cleanup_failure);
	fputs("}\n", file);
	return ferror(file) ? -EIO : 0;
}

static int64_t now_ms(void)
{
	struct timespec now;

	clock_gettime(CLOCK_MONOTONIC, &now);
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int identity_failure(struct fttr_gem_job *job, int error)
{
	if (job->error || (job->phase >= FTTR_GEM_CLEANUP_PROBE && job->phase <= FTTR_GEM_DELETE_VERIFY) ||
	    job->phase == FTTR_GEM_RELEASED)
		return cleanup_failure(job, error);
	job->error = error;
	job->failure = job->me;
	job->phase = FTTR_GEM_FAILED;
	return error;
}

int fttr_gem_execute(struct fttr_omci_session *session, struct fttr_gem_job *job,
		     int directory, const struct fttr_onu_state *identity)
{
	struct pollfd descriptor = { .fd = session->fd, .events = POLLIN };
	struct fttr_omci_message message;
	uint8_t frame[1996];
	int action = job->phase >= FTTR_GEM_CONFIGURED ?
		fttr_gem_job_update(job, &session->transaction) : FTTR_GEM_REQUEST;
	int ret;
	int64_t now;

	for (;;) {
		while (action == FTTR_GEM_REQUEST) {
			ret = fttr_miniolt_same(directory, session->onu, identity);
			if (ret)
				return identity_failure(job, ret);
			fttr_me_job_send(&job->me, session->fd, &session->link, now_ms());
			action = fttr_gem_job_update(job, &session->transaction);
		}
		if (action < 0)
			return action;
		if (action == FTTR_GEM_COMPLETE) {
			ret = fttr_miniolt_same(directory, session->onu, identity);
			return ret ? identity_failure(job, ret) : 0;
		}
		now = now_ms();
		if (now >= job->me.deadline && job->me.attempts < 3) {
			ret = fttr_miniolt_same(directory, session->onu, identity);
			if (ret)
				return identity_failure(job, ret);
			now = now_ms();
		}
		fttr_me_job_timeout(&job->me, session->fd, &session->link, now);
		action = fttr_gem_job_update(job, &session->transaction);
		if (action)
			continue;
		ret = poll(&descriptor, 1, job->me.deadline - now);
		if (ret < 0 || (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL))) {
			job->me.error = ret < 0 ? -errno : -EIO;
			job->me.state = FTTR_ME_FAILED;
		} else if (!ret) {
			continue;
		} else {
			ret = fttr_omci_receive(session->fd, session->link.ethertype, frame, sizeof(frame), &message);
			if (ret == -EAGAIN)
				continue;
			if (ret) {
				job->me.error = ret;
				job->me.state = FTTR_ME_FAILED;
			} else if (!fttr_me_job_reply(&job->me, &message)) {
				continue;
			}
		}
		action = fttr_gem_job_update(job, &session->transaction);
	}
}
