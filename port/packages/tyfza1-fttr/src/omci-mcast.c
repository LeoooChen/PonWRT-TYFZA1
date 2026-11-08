// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <string.h>

#include "omci-mcast.h"

/* Original e06a0: GEM4095, IPv4 range224.0.1.0..239.255.255.255. */
static const uint8_t ipv4_row[12] = { 0x0f, 0xff, 0, 0, 0xe0, 0, 1, 0, 0xef, 0xff, 0xff, 0xff };

static void be16(uint8_t *p, uint16_t value)
{
	p[0] = value >> 8;
	p[1] = value;
}

void fttr_mcast_start(struct fttr_mcast_job *job, uint16_t onu, uint16_t *counter,
		uint16_t uni_port, uint16_t port, uint8_t number, bool extended,
		const uint8_t serial[8])
{
	struct fttr_omci_object *o;

	memset(job, 0, sizeof(*job));
	job->objects[0] = (struct fttr_omci_object) { .class_id = 268, .instance = 4095,
		.create = { .mask = 0xfac0, .size = 14, .value = { 0x0f, 0xff, 0, 0, 2 } } };
	job->objects[1] = (struct fttr_omci_object) { .class_id = 281, .instance = 4095, .dependencies = 1,
		.create = { .mask = 0xf300, .size = 10, .value = { 0x0f, 0xff, 0, 0, 0, 0, 0, 0, 1, 0 } } };
	/* Actual ordinary/CTC profile producers e5bfc/e69ac, original pool defaults. */
	o = &job->objects[2];
	*o = (struct fttr_omci_object) { .class_id = extended ? 65531 : 309, .instance = uni_port,
		.create = { .mask = extended ? 0xfc59 : 0xfc78, .size = extended ? 22 : 23,
			.value = { 2, 0, 1, 0, 0, 0, 0, 0, 0, 16, 2 } },
		.set = { .mask = extended ? 2 : 1, .size = extended ? 1 : 3, .value = { extended ? 1 : 0 } } };
	o->create.value[extended ? 14 : 18] = 125;
	o->create.value[extended ? 18 : 22] = 100;
	job->objects[3] = (struct fttr_omci_object) { .class_id = 310, .instance = uni_port, .dependencies = 1U << 2,
		.create = { .mask = 0xf800, .size = 10, .value = { 0, 0, 0, 0, 4, 0, 0, 0x5d, 0xc0, 1 } } };
	be16(job->objects[3].create.value + 1, uni_port);
	job->objects[4] = (struct fttr_omci_object) { .class_id = 311, .instance = uni_port, .dependencies = 1U << 3,
		.create = { .mask = 0x8000, .size = 1 } };
	job->objects[5] = (struct fttr_omci_object) { .class_id = 47, .instance = port, .dependencies = 1U << 1,
		.create = { .mask = 0xfc00, .size = 10, .value = { 0x80, 0x80, number, 6, 0x0f, 0xff, 0, 0, 0, 1 } } };
	job->objects[6] = (struct fttr_omci_object) { .class_id = 84, .instance = port, .dependencies = 1U << 5,
		.create = { .mask = 0x4000, .size = 1 } };
	fttr_objects_job_start(&job->transaction, onu, counter, job->objects, 7, serial);
	job->transaction.defer_apply = true;
}

static int table_request(struct fttr_mcast_job *job, uint16_t *counter, bool next, const uint8_t *row)
{
	uint8_t sequence[2] = { 0 };

	fttr_me_job_start(&job->table, job->transaction.me.onu, counter,
		row ? FTTR_ME_SET : next ? FTTR_ME_GET_NEXT : FTTR_ME_GET, 281, 4095, 0x0080,
		row ? row : next ? sequence : NULL, row ? 12 : next ? 2 : 0);
	job->table.no_retry = job->stage == FTTR_MCAST_DELETE_ROW;
	return 1;
}

struct fttr_me_job *fttr_mcast_pending(struct fttr_mcast_job *job)
{
	switch (job->stage) {
	case FTTR_MCAST_CHECK: case FTTR_MCAST_APPLY: case FTTR_MCAST_RELEASE:
		return &job->transaction.me;
	case FTTR_MCAST_READY: case FTTR_MCAST_FAILED: case FTTR_MCAST_RELEASED: case FTTR_MCAST_CHECKED:
		return NULL;
	default: return &job->table;
	}
}

static int failed(struct fttr_mcast_job *job, int error, bool cleanup)
{
	if (cleanup)
		job->cleanup_error = error;
	else if (!job->error) {
		job->error = error;
		job->failure = job->stage == FTTR_MCAST_CHECK || job->stage == FTTR_MCAST_APPLY ?
			job->transaction.failure : job->table;
	}
	job->configured = false;
	job->stage = FTTR_MCAST_FAILED;
	return job->error ? job->error : error;
}

static int release_objects(struct fttr_mcast_job *job, uint16_t *counter)
{
	int ret = fttr_objects_job_release(&job->transaction, counter);

	if (ret < 0)
		return failed(job, ret, true);
	job->stage = FTTR_MCAST_RELEASE;
	return ret == 1 ? 1 : fttr_mcast_update(job, counter);
}

static int remove_row(struct fttr_mcast_job *job, uint16_t *counter)
{
	uint8_t empty[12] = { 0x0f, 0xff, 0, 0 };

	if (!job->table_sent)
		return release_objects(job, counter);
	job->stage = FTTR_MCAST_DELETE_ROW;
	return table_request(job, counter, false, empty);
}

static int rollback(struct fttr_mcast_job *job, uint16_t *counter, int error)
{
	job->error = error;
	job->failure = job->table;
	if (!job->table_sent)
		return release_objects(job, counter);
	/* The only writable initial table is proven empty. Remove only our key,
	 * then prove the original empty table before releasing dependencies.
	 */
	job->stage = FTTR_MCAST_RELEASE_TABLE;
	return table_request(job, counter, false, NULL);
}

static int release_check(struct fttr_mcast_job *job, uint16_t *counter)
{
	if (job->recover_apply) {
		while (job->transaction.index < job->transaction.count &&
		       !job->objects[job->transaction.index].touched) job->transaction.index++;
		if (job->transaction.index == job->transaction.count) {
			if (!job->table_sent || job->objects[1].presence == FTTR_OBJECT_ABSENT)
				return release_objects(job, counter);
			job->stage = FTTR_MCAST_RELEASE_TABLE;
			return table_request(job, counter, false, NULL);
		}
	}
	const struct fttr_omci_object *object = &job->objects[job->transaction.index];
	const struct fttr_omci_values *values = job->transaction.group ? &object->set : &object->create;

	fttr_me_job_start(&job->table, job->transaction.me.onu, counter, FTTR_ME_GET,
		object->class_id, object->instance, values->mask, NULL, 0);
	return 1;
}

int fttr_mcast_release(struct fttr_mcast_job *job, uint16_t *counter)
{
	if (job->stage != FTTR_MCAST_READY || !job->configured || job->error)
		return -EBUSY;
	job->configured = false;
	job->releasing = true;
	/* Verify dependencies before the first destructive write, including the
	 * IPv4 row deletion. A later object check would discover drift too late.
	 */
	job->stage = FTTR_MCAST_RELEASE_CHECK;
	job->transaction.index = job->transaction.group = 0;
	return release_check(job, counter);
}

void fttr_mcast_cancel(struct fttr_mcast_job *job, int error)
{
	struct fttr_me_job *me = fttr_mcast_pending(job);

	if (job->stage == FTTR_MCAST_SET && me->attempts)
		job->table_sent = true;
	if (me == &job->transaction.me) {
		struct fttr_objects_job *objects = &job->transaction;

		if (objects->phase == FTTR_OBJECT_CREATE && me->attempts) {
			objects->objects[objects->index].touched = true;
			objects->objects[objects->index].presence = FTTR_OBJECT_UNKNOWN;
		}
		if (job->error || job->releasing) {
			objects->cleanup_error = error;
			objects->cleanup_failure = *me;
		} else {
			objects->error = error;
			objects->failure = *me;
		}
		objects->phase = FTTR_OBJECT_FAILED;
	}
	if (me) { me->state = FTTR_ME_FAILED; me->error = error; }
	failed(job, error, job->error || job->releasing);
}

int fttr_mcast_apply(struct fttr_mcast_job *job, uint16_t *counter)
{
	if (job->stage != FTTR_MCAST_CHECKED)
		return -EINVAL;
	job->stage = FTTR_MCAST_APPLY;
	return fttr_objects_job_apply(&job->transaction, counter);
}

static int checked(struct fttr_mcast_job *job, uint16_t *counter)
{
	job->stage = FTTR_MCAST_CHECKED;
	return job->defer_apply ? 2 : fttr_mcast_apply(job, counter);
}

int fttr_mcast_update(struct fttr_mcast_job *job, uint16_t *counter)
{
	uint32_t length;
	int ret;

again:
	switch (job->stage) {
	case FTTR_MCAST_READY: case FTTR_MCAST_RELEASED: case FTTR_MCAST_CHECKED: return 2;
	case FTTR_MCAST_FAILED: return job->error ? job->error : job->cleanup_error;
	case FTTR_MCAST_CHECK:
	case FTTR_MCAST_APPLY:
	case FTTR_MCAST_RELEASE:
		if (job->transaction.phase != FTTR_OBJECT_RELEASED && job->transaction.phase != FTTR_OBJECT_FAILED &&
		    job->transaction.me.state == FTTR_ME_WAIT && !job->transaction.me.attempts)
			return 1;
		ret = fttr_objects_job_update(&job->transaction, counter);
		if (ret < 0) {
			job->cleanup_error = job->transaction.cleanup_error;
			return failed(job, job->error ? job->error : ret, job->releasing);
		}
		if (ret != 2)
			return ret;
		if (job->stage == FTTR_MCAST_RELEASE) {
			job->stage = job->error ? FTTR_MCAST_FAILED : FTTR_MCAST_RELEASED;
			return job->error ? job->error : 2;
		}
		if (job->stage == FTTR_MCAST_CHECK) {
			if (job->objects[1].presence != FTTR_OBJECT_ABSENT) {
				job->stage = FTTR_MCAST_PRETABLE;
				return table_request(job, counter, false, NULL);
			}
			ret = checked(job, counter);
			if (job->defer_apply) return ret;
			if (ret == 1) return ret;
			goto again;
		}
		job->stage = job->objects[1].touched ? FTTR_MCAST_EMPTY : FTTR_MCAST_READ;
		return table_request(job, counter, false, NULL);
	default: break;
	}
	if (job->stage == FTTR_MCAST_SET && job->table.attempts)
		job->table_sent = true;
	if (job->table.state == FTTR_ME_WAIT)
		return job->table.attempts ? 0 : 1;
	if (job->table.error && !(job->recover_release && job->stage == FTTR_MCAST_RELEASE_CHECK &&
	    !job->transaction.group && job->objects[job->transaction.index].touched &&
	    job->table.error == -EREMOTEIO && job->table.result == 5)) {
		if (job->stage == FTTR_MCAST_DELETE_ROW && job->table.error == -ETIMEDOUT) {
			/* Retrying this Set against an already absent original row would
			 * add a zero-address ACL entry. Resolve the one send by readback.
			 */
			job->stage = FTTR_MCAST_VERIFY_EMPTY;
			return table_request(job, counter, false, NULL);
		}
		if (job->stage == FTTR_MCAST_PRETABLE)
			return failed(job, job->table.error, false);
		if (job->releasing || job->error)
			return failed(job, job->table.error, true);
		return rollback(job, counter, job->table.error);
	}
	if (job->stage == FTTR_MCAST_RELEASE_CHECK) {
		struct fttr_omci_object *object = &job->objects[job->transaction.index];
		const struct fttr_omci_values *values = job->transaction.group ? &object->set : &object->create;

		bool absent = job->table.error == -EREMOTEIO && job->table.result == 5;
		if (absent) {
			object->presence = FTTR_OBJECT_ABSENT; object->touched = false;
		} else if (memcmp(job->table.reply + 11, values->value, values->size))
			return failed(job, -EUCLEAN, true);
		if (!absent && !job->transaction.group && object->set.mask) {
			job->transaction.group = 1;
			return release_check(job, counter);
		}
		job->transaction.group = 0;
		if (++job->transaction.index < job->transaction.count)
			return release_check(job, counter);
		if ((job->recover_apply && !job->table_sent) ||
		    (job->recover_release && job->objects[1].presence == FTTR_OBJECT_ABSENT)) {
			/* This table died with its owned IW. Never replay the original
			 * ambiguous delete Set against an absent row or instance. */
			job->table_sent = false;
			return release_objects(job, counter);
		}
		job->stage = FTTR_MCAST_RELEASE_TABLE;
		return table_request(job, counter, false, NULL);
	}
	if (job->stage == FTTR_MCAST_SET || job->stage == FTTR_MCAST_DELETE_ROW) {
		job->stage = job->stage == FTTR_MCAST_SET ? FTTR_MCAST_READ : FTTR_MCAST_VERIFY_EMPTY;
		return table_request(job, counter, false, NULL);
	}
	if (job->table.request[2] == FTTR_ME_GET_NEXT) {
		memcpy(job->table_row, job->table.reply + 11, sizeof(job->table_row));
		if (memcmp(job->table_row, ipv4_row, sizeof(ipv4_row))) {
			if (job->releasing || job->error)
				return failed(job, -EUCLEAN, true);
			return job->stage == FTTR_MCAST_PRETABLE ? failed(job, -EEXIST, false) : rollback(job, counter, -EUCLEAN);
		}
		if (job->stage == FTTR_MCAST_PRETABLE) {
			ret = checked(job, counter);
			if (job->defer_apply) return ret;
			if (ret == 1) return ret;
			goto again;
		}
		if (job->stage == FTTR_MCAST_RELEASE_TABLE)
			return remove_row(job, counter);
		job->configured = true;
		job->stage = FTTR_MCAST_READY;
		return 2;
	}
	length = (uint32_t)job->table.reply[11] << 24 | (uint32_t)job->table.reply[12] << 16 |
		(uint32_t)job->table.reply[13] << 8 | job->table.reply[14];
	job->table_length = length;
	if (job->stage == FTTR_MCAST_VERIFY_EMPTY) {
		if (length) return failed(job, -EUCLEAN, true);
		job->table_sent = false;
		return release_objects(job, counter);
	}
	if (job->stage == FTTR_MCAST_PRETABLE && !length) {
		job->prior_empty = true;
		ret = checked(job, counter);
		if (job->defer_apply) return ret;
		if (ret == 1) return ret;
		goto again;
	}
	if (job->stage == FTTR_MCAST_EMPTY || (job->stage == FTTR_MCAST_READ && !job->table_sent && job->prior_empty)) {
		if (length) return rollback(job, counter, -EEXIST);
		job->prior_empty = true;
		job->stage = FTTR_MCAST_SET;
		return table_request(job, counter, false, ipv4_row);
	}
	if (job->stage == FTTR_MCAST_RELEASE_TABLE && (job->error || (job->recover_release && job->prior_empty && job->table_sent)) && !length) {
		/* The original ONU inserts a zero-address entry for a delete key
		 * which is already absent. A proven empty table needs no Set.
		 */
		job->table_sent = false;
		return release_objects(job, counter);
	}
	/* The retained ONU's snapshot reports24 bytes but writes12 per IPv4 row.
	 * Infer that defect only from our exclusive empty -> single-row write,
	 * never from restart adoption. No uninitialized trailing bytes are read.
	 */
	if ((job->stage == FTTR_MCAST_READ || job->stage == FTTR_MCAST_RELEASE_TABLE) &&
	    job->objects[1].touched && job->prior_empty && job->table_sent && length == 24)
		job->doubled = true;
	if (length != (job->doubled ? 24U : 12U)) {
		if (job->stage == FTTR_MCAST_PRETABLE)
			return failed(job, -ENOTRECOVERABLE, false);
		if (job->error || job->releasing) return failed(job, -EUCLEAN, true);
		return rollback(job, counter, -EUCLEAN);
	}
	return table_request(job, counter, true, NULL);
}

int fttr_mcast_dump(const struct fttr_mcast_job *job, FILE *file)
{
	fprintf(file, "{\"event\":\"service_multicast\",\"phase\":%u,\"configured\":%s,\"error\":%d,"
		"\"cleanup_error\":%d,\"table_length\":%u,\"table_sent\":%s,\"prior_empty\":%s,\"doubled\":%s}\n",
		job->stage, job->configured ? "true" : "false", job->error, job->cleanup_error,
		job->table_length, job->table_sent ? "true" : "false", job->prior_empty ? "true" : "false",
		job->doubled ? "true" : "false");
	return fttr_objects_job_dump(&job->transaction, file, "service_multicast_objects");
}
