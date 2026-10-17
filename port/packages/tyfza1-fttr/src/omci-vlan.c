// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "omci-vlan.h"

static uint16_t be16(const uint8_t *p)
{
	return (uint16_t)p[0] << 8 | p[1];
}

static uint32_t be32(const uint8_t *p)
{
	return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static void put32(uint8_t *p, uint32_t value)
{
	p[0] = value >> 24;
	p[1] = value >> 16;
	p[2] = value >> 8;
	p[3] = value;
}

int fttr_vlan_plan_build(struct fttr_vlan_plan *plan, enum fttr_vlan_policy policy,
			uint16_t input_vid, uint16_t output_vid, uint8_t priority, bool remove)
{
	unsigned int i;

	memset(plan, 0, sizeof(*plan));
	switch (policy) {
	case FTTR_VLAN_DISCARD_ALL:
	case FTTR_VLAN_FORWARD_ALL:
	case FTTR_VLAN_DISCARD_UNTAGGED:
	case FTTR_VLAN_DISCARD_TAGGED:
		plan->count = policy == FTTR_VLAN_DISCARD_UNTAGGED ? 1 :
			policy == FTTR_VLAN_DISCARD_TAGGED ? 2 : 3;
		for (i = 0; i < plan->count; i++) {
			unsigned int rule = i + (policy == FTTR_VLAN_DISCARD_TAGGED ? 1 : 0);

			put32(plan->rows[i], rule == 2 ? 0xe8000000 : 0xf8000000);
			put32(plan->rows[i] + 4, rule == 0 ? 0xf8000000 : 0xe8000000);
			put32(plan->rows[i] + 8, policy == FTTR_VLAN_FORWARD_ALL ? 0x000f0000 : 0xc00f0000);
			put32(plan->rows[i] + 12, 0x000f0000);
		}
		break;
	case FTTR_VLAN_TRANSLATE:
		if (input_vid > 4094 || output_vid > 4094)
			return -EINVAL;
		plan->count = 2;
		put32(plan->rows[0], 0xf8000000);
		put32(plan->rows[0] + 4, 0x80005000 | (uint32_t)input_vid << 15);
		put32(plan->rows[0] + 8, 0x400f0000);
		put32(plan->rows[0] + 12, 0x00080004 | (uint32_t)output_vid << 3);
		put32(plan->rows[1], 0x80005000 | (uint32_t)input_vid << 15);
		put32(plan->rows[1] + 4, 0x88000000);
		put32(plan->rows[1] + 8, 0x40090002 | (uint32_t)output_vid << 3);
		put32(plan->rows[1] + 12, 0x000f0000);
		break;
	case FTTR_VLAN_TAG_UNTAGGED:
		if (output_vid > 4094 || priority > 7)
			return -EINVAL;
		plan->count = 1;
		put32(plan->rows[0], 0xf8000000);
		put32(plan->rows[0] + 4, 0xf8000000);
		put32(plan->rows[0] + 8, 0x000f0000);
		put32(plan->rows[0] + 12, (uint32_t)priority << 16 | (uint32_t)output_vid << 3 | 4);
		break;
	default:
		return -EINVAL;
	}
	if (remove)
		for (i = 0; i < plan->count; i++)
			memset(plan->rows[i] + 8, 0xff, 8);
	return 0;
}

void fttr_vlan_read_start(struct fttr_vlan_read_job *job, uint16_t onu,
			 uint16_t *counter, uint16_t instance)
{
	memset(job, 0, sizeof(*job));
	fttr_me_job_start(&job->me, onu, counter, FTTR_ME_GET, 171, instance, 0x4000, NULL, 0);
}

static int read_fail(struct fttr_vlan_read_job *job, int error)
{
	job->phase = FTTR_VLAN_READ_FAILED;
	job->error = error;
	return error;
}

int fttr_vlan_read_update(struct fttr_vlan_read_job *job, uint16_t *counter)
{
	struct fttr_vlan_table *table = &job->table;
	uint16_t onu = job->me.onu, instance = be16(job->me.request + 6), sequence;
	uint8_t value[2];
	uint32_t size;

	if (job->phase == FTTR_VLAN_READ_FAILED)
		return job->error;
	if (job->phase == FTTR_VLAN_READ_DONE)
		return 0;
	if (job->me.state == FTTR_ME_FAILED)
		return read_fail(job, job->me.error);
	if (job->me.state == FTTR_ME_WAIT)
		return 1;
	if (job->phase == FTTR_VLAN_CAPACITY) {
		table->capacity = be16(job->me.reply + 11);
		job->phase = FTTR_VLAN_LENGTH;
		fttr_me_job_start(&job->me, onu, counter, FTTR_ME_GET, 171, instance, 0x0400, NULL, 0);
		return 1;
	}
	if (job->phase == FTTR_VLAN_LENGTH) {
		table->length = be32(job->me.reply + 11);
		if (table->length % 16)
			return read_fail(job, -EPROTO);
		if (table->length > (uint32_t)table->capacity * 16)
			return read_fail(job, -EMSGSIZE);
		if (table->length) {
			table->data = malloc(table->length);
			if (!table->data)
				return read_fail(job, -ENOMEM);
		}
		job->phase = FTTR_VLAN_NEXT;
	} else {
		size = table->length - table->received;
		if (size > 29)
			size = 29;
		memcpy(table->data + table->received, job->me.reply + 11, size);
		table->received += size;
	}
	if (table->received == table->length) {
		table->complete = true;
		job->phase = FTTR_VLAN_READ_DONE;
		return 0;
	}
	sequence = table->received / 29;
	value[0] = sequence >> 8;
	value[1] = sequence;
	fttr_me_job_start(&job->me, onu, counter, FTTR_ME_GET_NEXT, 171, instance, 0x0400, value, 2);
	return 1;
}

int fttr_vlan_read(struct fttr_omci_session *session, uint16_t instance, int directory,
		   const struct fttr_onu_state *identity, struct fttr_vlan_read_job *job)
{
	int ret;

	fttr_vlan_read_start(job, session->onu, &session->transaction, instance);
	while ((ret = fttr_vlan_read_update(job, &session->transaction)) == 1)
		fttr_me_exchange_same(session, &job->me, directory, identity);
	return ret;
}

static bool deletion(const uint8_t row[16])
{
	return be32(row + 8) == UINT32_MAX && be32(row + 12) == UINT32_MAX;
}

static const uint8_t *find(const struct fttr_vlan_table *table, const uint8_t key[8])
{
	uint32_t offset;

	for (offset = 0; offset < table->length; offset += 16)
		if (!memcmp(table->data + offset, key, 8))
			return table->data + offset;
	return NULL;
}

static bool matches(const uint8_t *observed, const uint8_t expected[16])
{
	return deletion(expected) ? observed == NULL : observed && !memcmp(observed, expected, 16);
}

static int verify(const struct fttr_vlan_table *before, const struct fttr_vlan_table *after,
		  const struct fttr_vlan_plan *plan)
{
	uint32_t offset, count = before->length / 16;
	unsigned int i;

	for (i = 0; i < plan->count; i++) {
		const uint8_t *row = plan->rows[i], *previous = find(before, row);

		if (!matches(find(after, row), row))
			return -EUCLEAN;
		if (deletion(row))
			count -= previous != NULL;
		else
			count += previous == NULL;
	}
	if (count != after->length / 16)
		return -EUCLEAN;
	for (offset = 0; offset < before->length; offset += 16) {
		const uint8_t *previous = before->data + offset;

		for (i = 0; i < plan->count && memcmp(plan->rows[i], previous, 8); i++)
			;
		if (i == plan->count && !matches(find(after, previous), previous))
			return -EUCLEAN;
	}
	return 0;
}

static int set_row(struct fttr_omci_session *session, uint16_t instance, const uint8_t row[16],
		   int directory, const struct fttr_onu_state *identity, struct fttr_me_job *me)
{
	fttr_me_job_start(me, session->onu, &session->transaction, FTTR_ME_SET, 171, instance, 0x0400, row, 16);
	return fttr_me_exchange_same(session, me, directory, identity);
}

int fttr_vlan_apply(struct fttr_omci_session *session, uint16_t instance,
		   const struct fttr_vlan_plan *plan, int directory,
		   const struct fttr_onu_state *identity, struct fttr_vlan_change *change)
{
	struct fttr_vlan_table *before = &change->before.table, *after = &change->after.table;
	struct fttr_vlan_plan restore = { 0 };
	uint32_t offset;
	int i, count, ret;

	memset(change, 0, sizeof(*change));
	change->phase = "read";
	ret = fttr_vlan_read(session, instance, directory, identity, &change->before);
	if (ret) {
		change->failure = change->before.me;
		goto failed;
	}
	/* Duplicate keys make replacement/rollback ambiguous. Retain the actual table. */
	for (offset = 0; offset < before->length; offset += 16)
		if (find(before, before->data + offset) != before->data + offset) {
			ret = -EUCLEAN;
			goto failed;
		}
	count = before->length / 16;
	for (i = 0; i < plan->count; i++) {
		bool exists = find(before, plan->rows[i]) != NULL;

		count += deletion(plan->rows[i]) ? -(int)exists : !exists;
	}
	if (count > before->capacity) {
		ret = -ENOSPC;
		goto failed;
	}
	change->phase = "set";
	for (i = 0; i < plan->count; i++) {
		const uint8_t *row = plan->rows[i];

		if (matches(find(before, row), row))
			continue;
		ret = set_row(session, instance, row, directory, identity, &change->me);
		if (change->me.attempts)
			change->sent |= 1U << i;
		if (ret) {
			change->failure = change->me;
			goto failed;
		}
	}
	change->phase = "verify";
	ret = fttr_vlan_read(session, instance, directory, identity, &change->after);
	if (!ret)
		ret = verify(before, after, plan);
	if (ret) {
		change->failure = change->after.me;
		goto failed;
	}
	change->phase = "configured";
	change->verified = true;
	return 0;

failed:
	change->error = ret;
	if (!change->sent)
		return ret;
	/* Re-read the real table before deciding which prior rows need restoration. */
	free(after->data);
	change->cleanup_error = fttr_vlan_read(session, instance, directory, identity, &change->after);
	if (change->cleanup_error) {
		change->cleanup_failure = change->after.me;
		return ret;
	}
	for (i = plan->count - 1; i >= 0; i--) {
		uint8_t *row;
		const uint8_t *previous;

		if (!(change->sent & (1U << i)))
			continue;
		row = restore.rows[restore.count++];
		previous = find(before, plan->rows[i]);
		if (previous)
			memcpy(row, previous, 16);
		else {
			memcpy(row, plan->rows[i], 8);
			memset(row + 8, 0xff, 8);
		}
		if (matches(find(after, row), row))
			continue;
		after->complete = false;
		change->cleanup_error = set_row(session, instance, row, directory, identity, &change->me);
		if (change->cleanup_error) {
			change->cleanup_failure = change->me;
			return ret;
		}
	}
	free(after->data);
	change->cleanup_error = fttr_vlan_read(session, instance, directory, identity, &change->after);
	if (!change->cleanup_error)
		change->cleanup_error = verify(before, after, &(struct fttr_vlan_plan){ 0 });
	if (change->cleanup_error)
		change->cleanup_failure = change->after.me;
	else
		change->restored = true;
	return ret;
}
