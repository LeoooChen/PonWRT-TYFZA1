// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <poll.h>
#include <string.h>
#include <time.h>

#include "omci-objects.h"
#include "omci-socket.h"

/* Original peer result table at 169db0: code5 is unknown ME instance. */
#define OMCI_UNKNOWN_INSTANCE 5

static int request(struct fttr_objects_job *job, uint16_t *counter, enum fttr_me_operation operation)
{
	struct fttr_omci_object *object = &job->objects[job->index];
	const struct fttr_omci_values *values = job->group ? &object->set : &object->create;
	bool write = operation == FTTR_ME_CREATE || operation == FTTR_ME_SET;

	/* Constructors split attributes into groups within the baseline budgets. */
	fttr_me_job_start(&job->me, job->me.onu, counter, operation, object->class_id,
			 object->instance, values->mask, write ? values->value : NULL,
			 write ? values->size : 0);
	return FTTR_OBJECT_REQUEST;
}

static int next_create(struct fttr_objects_job *job, uint16_t *counter)
{
	while (job->index < job->count) {
		if (job->objects[job->index].presence == FTTR_OBJECT_ABSENT) {
			job->phase = FTTR_OBJECT_CREATE;
			job->group = 0;
			return request(job, counter, FTTR_ME_CREATE);
		}
		job->index++;
	}
	job->phase = FTTR_OBJECT_CONFIGURED;
	return FTTR_OBJECT_COMPLETE;
}

static int next_cleanup(struct fttr_objects_job *job, uint16_t *counter)
{
	while (job->index >= 0) {
		struct fttr_omci_object *object = &job->objects[job->index];

		if (object->touched) {
			bool unknown = object->presence == FTTR_OBJECT_UNKNOWN;

			job->phase = unknown ? FTTR_OBJECT_CLEANUP_PROBE : FTTR_OBJECT_DELETE;
			job->group = 0;
			return request(job, counter, unknown ? FTTR_ME_GET : FTTR_ME_DELETE);
		}
		job->index--;
	}
	job->phase = job->error ? FTTR_OBJECT_FAILED : FTTR_OBJECT_RELEASED;
	return job->error ? job->error : FTTR_OBJECT_COMPLETE;
}

static int failure(struct fttr_objects_job *job, uint16_t *counter, int error)
{
	job->error = error;
	job->failure = job->me;
	job->index = job->count - 1;
	return next_cleanup(job, counter);
}

static int cleanup_failure(struct fttr_objects_job *job, int error)
{
	job->cleanup_error = error;
	job->cleanup_failure = job->me;
	job->phase = FTTR_OBJECT_FAILED;
	return job->error ? job->error : error;
}

void fttr_objects_job_start(struct fttr_objects_job *job, uint16_t onu, uint16_t *counter,
		struct fttr_omci_object *objects, int count, const uint8_t serial[8])
{
	int i;

	memset(job, 0, sizeof(*job));
	job->me.onu = onu;
	job->objects = objects;
	job->count = count;
	memcpy(job->serial, serial, sizeof(job->serial));
	for (i = 0; i < count; i++) {
		objects[i].presence = FTTR_OBJECT_UNKNOWN;
		objects[i].touched = false;
	}
	request(job, counter, FTTR_ME_GET);
}

int fttr_objects_job_update(struct fttr_objects_job *job, uint16_t *counter)
{
	struct fttr_omci_object *object;
	const struct fttr_omci_values *values;
	int error = job->me.state == FTTR_ME_FAILED ? job->me.error : 0;
	int i;
	bool absent = error == -EREMOTEIO && job->me.result == OMCI_UNKNOWN_INSTANCE;

	if (job->phase == FTTR_OBJECT_CONFIGURED || job->phase == FTTR_OBJECT_RELEASED)
		return FTTR_OBJECT_COMPLETE;
	if (job->phase == FTTR_OBJECT_FAILED)
		return job->error ? job->error : job->cleanup_error;
	object = &job->objects[job->index];
	values = job->group ? &object->set : &object->create;
	if (job->phase == FTTR_OBJECT_CREATE && job->me.attempts) {
		object->touched = true;
		object->presence = FTTR_OBJECT_UNKNOWN;
	}
	if (job->me.state == FTTR_ME_WAIT)
		return 0;
	switch (job->phase) {
	case FTTR_OBJECT_CHECK:
		if (absent) {
			object->presence = FTTR_OBJECT_ABSENT;
		} else {
			if (error)
				return failure(job, counter, error);
			object->presence = FTTR_OBJECT_PRESENT;
			if (memcmp(job->me.reply + 11, values->value, values->size))
				return failure(job, counter, -EEXIST);
			if (!job->group && object->set.mask) {
				job->group = 1;
				return request(job, counter, FTTR_ME_GET);
			}
			object->presence = FTTR_OBJECT_VERIFIED;
		}
		job->group = 0;
		if (++job->index < job->count)
			return request(job, counter, FTTR_ME_GET);
		/* A reused dependent cannot own a newly created, missing prerequisite. */
		for (job->index = 0; job->index < job->count; job->index++)
			for (i = 0; i < job->count; i++)
				if (job->objects[job->index].presence == FTTR_OBJECT_VERIFIED &&
				    (job->objects[job->index].dependencies & (1U << i)) &&
				    job->objects[i].presence == FTTR_OBJECT_ABSENT)
					return failure(job, counter, -EUCLEAN);
		job->index = 0;
		return next_create(job, counter);
	case FTTR_OBJECT_CREATE:
		if (error)
			return failure(job, counter, error);
		object->presence = FTTR_OBJECT_PRESENT;
		if (object->set.mask) {
			job->phase = FTTR_OBJECT_CONFIGURE;
			job->group = 1;
			return request(job, counter, FTTR_ME_SET);
		}
		job->phase = FTTR_OBJECT_VERIFY;
		return request(job, counter, FTTR_ME_GET);
	case FTTR_OBJECT_CONFIGURE:
		if (error)
			return failure(job, counter, error);
		job->phase = FTTR_OBJECT_VERIFY;
		job->group = 0;
		return request(job, counter, FTTR_ME_GET);
	case FTTR_OBJECT_VERIFY:
		if (error)
			return failure(job, counter, error);
		object->presence = FTTR_OBJECT_PRESENT;
		if (memcmp(job->me.reply + 11, values->value, values->size))
			return failure(job, counter, -EUCLEAN);
		if (!job->group && object->set.mask) {
			job->group = 1;
			return request(job, counter, FTTR_ME_GET);
		}
		object->presence = FTTR_OBJECT_VERIFIED;
		job->index++;
		return next_create(job, counter);
	case FTTR_OBJECT_CLEANUP_PROBE:
	case FTTR_OBJECT_DELETE_VERIFY:
		if (absent) {
			object->presence = FTTR_OBJECT_ABSENT;
			object->touched = false;
			job->index--;
			return next_cleanup(job, counter);
		}
		if (error)
			return cleanup_failure(job, error);
		object->presence = FTTR_OBJECT_PRESENT;
		if (job->phase == FTTR_OBJECT_DELETE_VERIFY)
			return cleanup_failure(job, -EUCLEAN);
		job->phase = FTTR_OBJECT_DELETE;
		return request(job, counter, FTTR_ME_DELETE);
	case FTTR_OBJECT_DELETE:
		object->presence = FTTR_OBJECT_UNKNOWN;
		if (error && !absent)
			return cleanup_failure(job, error);
		job->phase = FTTR_OBJECT_DELETE_VERIFY;
		return request(job, counter, FTTR_ME_GET);
	default:
		return 0;
	}
}

int fttr_objects_job_release(struct fttr_objects_job *job, uint16_t *counter)
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

int fttr_objects_job_dump(const struct fttr_objects_job *job, FILE *file, const char *event)
{
	static const char * const phases[] = { "check", "create", "configure", "verify", "cleanup-probe",
		"delete", "delete-verify", "configured", "released", "failed" };
	static const char * const states[] = { "unknown", "absent", "present", "verified" };
	int i;

	fprintf(file, "{\"event\":\"%s\",\"id\":%u,\"serial\":\"", event, job->me.onu);
	for (i = 0; i < 8; i++)
		fprintf(file, "%02x", job->serial[i]);
	fprintf(file, "\",\"phase\":\"%s\",\"objects\":[", phases[job->phase]);
	for (i = 0; i < job->count; i++) {
		const struct fttr_omci_object *object = &job->objects[i];

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

static int identity_failure(struct fttr_objects_job *job, int error)
{
	if (job->error || (job->phase >= FTTR_OBJECT_CLEANUP_PROBE && job->phase <= FTTR_OBJECT_DELETE_VERIFY) ||
	    job->phase == FTTR_OBJECT_RELEASED)
		return cleanup_failure(job, error);
	job->error = error;
	job->failure = job->me;
	job->phase = FTTR_OBJECT_FAILED;
	return error;
}

int fttr_objects_execute(struct fttr_omci_session *session, struct fttr_objects_job *job,
		     int directory, const struct fttr_onu_state *identity)
{
	struct pollfd descriptor = { .fd = session->fd, .events = POLLIN };
	struct fttr_omci_message message;
	uint8_t frame[1996];
	int action = job->phase >= FTTR_OBJECT_CONFIGURED ?
		fttr_objects_job_update(job, &session->transaction) : FTTR_OBJECT_REQUEST;
	int ret;
	int64_t now;

	for (;;) {
		while (action == FTTR_OBJECT_REQUEST) {
			ret = fttr_miniolt_same(directory, session->onu, identity);
			if (ret)
				return identity_failure(job, ret);
			fttr_me_job_send(&job->me, session->fd, &session->link, now_ms());
			action = fttr_objects_job_update(job, &session->transaction);
		}
		if (action < 0)
			return action;
		if (action == FTTR_OBJECT_COMPLETE) {
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
		action = fttr_objects_job_update(job, &session->transaction);
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
		action = fttr_objects_job_update(job, &session->transaction);
	}
}
