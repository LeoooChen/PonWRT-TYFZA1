// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "omci-service.h"

static const struct fttr_mib_entity *entity(const struct fttr_mib_model *model, uint16_t c, uint16_t id)
{
	const struct fttr_mib_entity *e;

	for (e = model->entities; e; e = e->next)
		if (e->class_id == c && e->instance == id)
			return e;
	return NULL;
}

static int attr(const struct fttr_mib_entity *e, uint16_t mask, uint32_t *value)
{
	size_t size, i;
	const uint8_t *p = fttr_mib_entity_attr(e, mask, &size);

	if (!p)
		return -ENODATA;
	*value = 0;
	for (i = 0; i < size; i++)
		*value = (*value << 8) | p[i];
	return 0;
}

static uint16_t queue(const struct fttr_mib_model *model, uint16_t tcont, uint16_t priority)
{
	const struct fttr_mib_entity *e;
	uint32_t related;

	for (e = model->entities; e; e = e->next)
		if (e->class_id == 277 && (e->instance & 0x8000) && !attr(e, 0x0400, &related) &&
		    related == ((uint32_t)tcont << 16 | priority))
			return e->instance;
	return 0;
}

static int ports(struct fttr_service_job *job, const struct fttr_mib_model *model,
		 uint8_t *uni_number, uint8_t *ani_number)
{
	const struct fttr_mib_entity *e;
	bool used[256] = { 0 };
	uint32_t bridge, number, type, pointer;
	unsigned int id;

	for (e = model->entities; e; e = e->next) {
		if (e->class_id != 47)
			continue;
		if (attr(e, 0x8000, &bridge) || attr(e, 0x4000, &number) ||
		    attr(e, 0x2000, &type) || attr(e, 0x1000, &pointer))
			return -ENODATA;
		if (bridge != 0x8080)
			continue;
		used[number] = true;
		if (type == (job->config.veip ? 11U : 1U) && pointer == job->config.uni) {
			if (job->uni_port)
				return -EUCLEAN;
			job->uni_port = e->instance;
			*uni_number = number;
		}
		if (type == 5 && pointer == job->gem) {
			if (job->ani_port)
				return -EUCLEAN;
			job->ani_port = e->instance;
			*ani_number = number;
		}
	}
	for (id = 1; id <= 2048 && (!job->uni_port || !job->ani_port); id++) {
		if (entity(model, 47, id) || entity(model, 84, id) || entity(model, 171, id))
			continue;
		if (!job->uni_port)
			job->uni_port = id;
		else if (id != job->uni_port)
			job->ani_port = id;
	}
	if (!job->uni_port || !job->ani_port)
		return -ENOSPC;
	if (!*uni_number) {
		for (id = job->config.veip ? 9 : 1; id < 256 && used[id]; id++)
			;
		if (id == 256)
			return -ENOSPC;
		*uni_number = id;
		used[id] = true;
	}
	if (!*ani_number) {
		for (id = 1; id < 256 && used[id]; id++)
			;
		if (id == 256)
			return -ENOSPC;
		*ani_number = id;
	}
	return 0;
}

static void append(struct fttr_service_job *job, const struct fttr_objects_job *source)
{
	int offset = job->transaction.count, i;

	for (i = 0; i < source->count; i++) {
		job->objects[offset + i] = source->objects[i];
		job->objects[offset + i].dependencies <<= offset;
	}
	job->transaction.count += source->count;
}

int fttr_service_start(struct fttr_service_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_mib_model *model, const struct fttr_service_config *config,
		const uint8_t serial[8])
{
	struct fttr_association_job association;
	struct fttr_gem_job gem;
	struct fttr_profiles_config profiles = { .bridge = 0x8080 };
	struct fttr_uni_config uni = { .uni = config->uni, .bridge = 0x8080, .veip = config->veip };
	struct fttr_ani_config ani = { .bridge = 0x8080 };
	struct fttr_gem_config g = { .profile = 0x8080, .gal = 1, .bridge = true };
	const struct fttr_mib_entity *e, *assigned = NULL;
	uint16_t alloc = fttr_miniolt_alloc_id(onu, config->slot);
	uint32_t current;
	uint8_t uni_number = 0, ani_number = 0;
	int i, ret;

	memset(job, 0, sizeof(*job));
	job->phase = "select";
	job->transaction.me.onu = onu;
	memcpy(job->transaction.serial, serial, 8);
	job->config = *config;
	if (!entity(model, config->veip ? 329 : 11, config->uni))
		return job->error = -ENOENT;
	/* Never assign an already-owned Alloc-ID to a second T-CONT merely because
	 * the current owner lacks the requested priority queue. */
	for (e = model->entities; e; e = e->next)
		if (e->class_id == 262 && !attr(e, 0x8000, &current) && current == alloc) {
			if (assigned)
				return job->error = -EUCLEAN;
			assigned = e;
		}
	if (assigned) {
		job->tcont = assigned->instance;
		job->previous_alloc = alloc;
		job->queue = queue(model, job->tcont, config->priority);
	} else {
		/* Original ONU default row selects 00ff or ffff via gInfo+584. */
		for (e = model->entities; e; e = e->next)
			if (e->class_id == 262 && !attr(e, 0x8000, &current) &&
			    (current == 255 || current == 65535) &&
			    (job->queue = queue(model, e->instance, config->priority))) {
				job->tcont = e->instance;
				job->previous_alloc = current;
				break;
			}
	}
	if (!job->queue)
		return job->error = -ENOSPC;
	job->gem = fttr_miniolt_gem_id(onu, config->slot);
	ret = ports(job, model, &uni_number, &ani_number);
	if (ret)
		return job->error = ret;
	job->objects[0] = (struct fttr_omci_object) { .class_id = 262, .instance = job->tcont,
		.update_only = true, .create = { .mask = 0x8000, .size = 2, .value = { alloc >> 8, alloc } } };
	job->transaction.count = 1;
	fttr_profiles_job_start(&association, onu, counter, &profiles, serial);
	append(job, &association.transaction);
	g.entity = job->gem;
	g.tcont = job->tcont;
	g.queue = job->queue;
	fttr_gem_job_start(&gem, onu, counter, &g, serial);
	append(job, &gem.transaction);
	job->objects[3].dependencies |= 1;
	job->objects[4].dependencies |= (1U << 1) | (1U << 2);
	uni.entity = job->uni_port;
	fttr_uni_job_start(&association, onu, counter, &uni, serial);
	association.objects[0].create.value[2] = uni_number;
	append(job, &association.transaction);
	job->objects[5].dependencies |= 1U << 2;
	ani.entity = job->ani_port;
	ani.pointer = job->gem;
	ani.port = ani_number;
	ani.count = config->tagged ? 1 : 0;
	ani.vlans[0] = config->vid;
	fttr_ani_job_start(&association, onu, counter, &ani, serial);
	i = job->transaction.count;
	append(job, &association.transaction);
	job->objects[i].dependencies |= (1U << 2) | (1U << 4);
	job->objects[job->transaction.count++] = (struct fttr_omci_object) {
		.class_id = config->veip ? 329 : 11, .instance = config->uni, .update_only = true,
		.create = { .mask = config->veip ? 0x8000 : 0x0800, .size = 1, .value = { 0 } }
	};
	fttr_objects_job_start(&job->transaction, onu, counter, job->objects, job->transaction.count, serial);
	return 0;
}

static bool grant_equal(const struct fttr_tcont_grant *a, const struct fttr_tcont_grant *b)
{
	return a->fixed == b->fixed && a->limits == b->limits;
}

static int grant_write(struct fttr_omci_session *session, struct fttr_service_job *job,
		       int directory, const struct fttr_onu_state *identity,
		       const struct fttr_tcont_grant *value, bool *sent)
{
	int ret = fttr_miniolt_same(directory, session->onu, identity);

	if (!ret) {
		if (sent)
			*sent = true;
		ret = fttr_miniolt_tcont(directory, session->onu, job->config.slot,
			value->fixed, value->limits >> 16, value->limits & 0xffff);
	}
	if (!ret)
		ret = fttr_miniolt_tcont_read(directory, session->onu, job->config.slot, &job->observed);
	if (!ret && !grant_equal(value, &job->observed))
		ret = -EUCLEAN;
	if (!ret)
		ret = fttr_miniolt_same(directory, session->onu, identity);
	return ret;
}

int fttr_service_execute(struct fttr_omci_session *session, struct fttr_service_job *job,
			 int directory, const struct fttr_onu_state *identity)
{
	struct fttr_tcont_grant desired = { .fixed = job->config.fixed,
		.limits = (uint32_t)job->config.assured << 16 | job->config.maximum };
	struct fttr_vlan_plan plan;
	int ret;

	job->phase = "grant-read";
	ret = fttr_miniolt_tcont_read(directory, session->onu, job->config.slot, &job->previous);
	if (ret)
		return job->error = ret;
	job->observed = job->previous;
	if (job->previous.fixed > 0xffff)
		return job->error = -EUCLEAN;
	if ((job->previous.fixed || job->previous.limits) &&
	    job->previous_alloc != fttr_miniolt_alloc_id(session->onu, job->config.slot))
		return job->error = -EBUSY;
	job->phase = "objects";
	ret = fttr_objects_execute(session, &job->transaction, directory, identity);
	if (ret) {
		job->cleanup_error = job->transaction.cleanup_error;
		return job->error = ret;
	}
	job->phase = "grant";
	if (!grant_equal(&job->previous, &desired)) {
		ret = grant_write(session, job, directory, identity, &desired, &job->grant_sent);
		if (ret)
			goto failed;
	}
	job->grant_verified = true;
	job->phase = "vlan";
	if (job->config.tagged) {
		struct fttr_vlan_plan untagged;

		/* Original UNI mode1 b4c10: discard tagged, then tag untagged. */
		fttr_vlan_plan_build(&plan, FTTR_VLAN_DISCARD_TAGGED, 0, 0, 0, false);
		fttr_vlan_plan_build(&untagged, FTTR_VLAN_TAG_UNTAGGED,
			0, job->config.vid, job->config.priority, false);
		memcpy(plan.rows[plan.count++], untagged.rows[0], 16);
	} else {
		/* Original transparent mode4 b5704. */
		fttr_vlan_plan_build(&plan, FTTR_VLAN_FORWARD_ALL, 0, 0, 0, false);
	}
	ret = fttr_vlan_apply(session, job->uni_port, &plan, directory, identity, &job->vlan);
	if (ret) {
		if (job->vlan.cleanup_error) {
			job->cleanup_error = job->vlan.cleanup_error;
			if (ret == -ESTALE || job->cleanup_error == -ESTALE)
				job->grant_verified = false;
			return job->error = ret;
		}
		goto failed;
	}
	job->configured = true;
	job->phase = "configured";
	return 0;

failed:
	job->error = ret;
	job->grant_verified = false;
	if (job->grant_sent) {
		job->cleanup_error = grant_write(session, job, directory, identity, &job->previous, NULL);
		if (job->cleanup_error)
			return ret;
		job->grant_restored = true;
	}
	fttr_objects_job_release(&job->transaction, &session->transaction);
	job->cleanup_error = fttr_objects_execute(session, &job->transaction, directory, identity);
	return ret;
}

int fttr_service_dump(const struct fttr_service_job *job, FILE *file)
{
	int i;

	fprintf(file, "{\"event\":\"service_state\",\"id\":%u,\"serial\":\"", job->transaction.me.onu);
	for (i = 0; i < 8; i++)
		fprintf(file, "%02x", job->transaction.serial[i]);
	fprintf(file, "\",\"phase\":\"%s\",\"error\":%d,\"cleanup_error\":%d,"
		"\"configured\":%s,\"tcont\":%u,\"queue\":%u,\"gem\":%u,\"uni_port\":%u,\"ani_port\":%u,"
		"\"grant_sent\":%s,\"grant_verified\":%s,\"grant_restored\":%s,"
		"\"previous_grant\":[%u,%u],\"observed_grant\":[%u,%u],\"vlan_error\":%d,\"vlan_cleanup_error\":%d}\n",
		job->phase, job->error, job->cleanup_error, job->configured ? "true" : "false", job->tcont, job->queue,
		job->gem, job->uni_port, job->ani_port, job->grant_sent ? "true" : "false",
		job->grant_verified ? "true" : "false", job->grant_restored ? "true" : "false",
		job->previous.fixed, job->previous.limits, job->observed.fixed, job->observed.limits,
		job->vlan.error, job->vlan.cleanup_error);
	return ferror(file) ? -EIO : 0;
}

void fttr_service_clear(struct fttr_service_job *job)
{
	free(job->vlan.before.table.data);
	free(job->vlan.after.table.data);
}
