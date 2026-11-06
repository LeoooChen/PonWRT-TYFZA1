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
		 uint8_t *ani_number, uint8_t *mcast_number)
{
	const struct fttr_mib_entity *e;
	bool used[256] = { 0 }, occupied[2049] = { 0 };
	uint32_t bridge, number, type, pointer;
	unsigned int i, id;

	for (e = model->entities; e; e = e->next) {
		if ((e->class_id == 47 || e->class_id == 84 || e->class_id == 171) && e->instance <= 2048)
			occupied[e->instance] = true;
		if (e->class_id != 47)
			continue;
		if (attr(e, 0x8000, &bridge) || attr(e, 0x4000, &number) ||
		    attr(e, 0x2000, &type) || attr(e, 0x1000, &pointer))
			return -ENODATA;
		if (job->config.multicast && type == 6 && pointer == 4095 && bridge != 0x8080)
			return -EEXIST;
		for (i = 0; i < job->uni_count; i++) {
			struct fttr_service_uni *uni = &job->unis[i];

			if (type != (uni->veip ? 11U : 1U) || pointer != uni->instance)
				continue;
			/* A UNI belonging to another bridge cannot be silently duplicated. */
			if (bridge != 0x8080 || uni->port)
				return -EEXIST;
			uni->port = e->instance;
			uni->number = number;
		}
		if (bridge != 0x8080)
			continue;
		if (!number || number > 255 || used[number] || !entity(model, 45, bridge))
			return -EUCLEAN;
		used[number] = true;
		if (type == 5 && pointer == job->gem) {
			if (job->ani_port)
				return -EUCLEAN;
			job->ani_port = e->instance;
			*ani_number = number;
		}
		if (job->config.multicast && type == 6 && pointer == 4095) {
			if (job->mcast_port)
				return -EUCLEAN;
			job->mcast_port = e->instance;
			*mcast_number = number;
		}
	}
	/* Reserve every chosen entity and bridge port before constructing IO. */
	for (i = 0; i <= job->uni_count + !!job->config.multicast; i++) {
		uint16_t *port = i < job->uni_count ? &job->unis[i].port :
			i == job->uni_count ? &job->ani_port : &job->mcast_port;
		uint8_t *number = i < job->uni_count ? &job->unis[i].number :
			i == job->uni_count ? ani_number : mcast_number;

		if (!*port) {
			for (id = 1; id <= 2048 && occupied[id]; id++)
				;
			if (id > 2048)
				return -ENOSPC;
			*port = id;
			occupied[id] = true;
		}
		if (!*number) {
			for (id = i < job->uni_count && job->unis[i].veip ? 9 : 1; id < 256 && used[id]; id++)
				;
			if (id == 256)
				return -ENOSPC;
			*number = id;
			used[id] = true;
		}
	}
	job->uni_port = job->unis[0].port;
	return 0;
}

static int select_unis(struct fttr_service_job *job, const struct fttr_mib_model *model)
{
	const struct fttr_service_config *config = &job->config;
	const struct fttr_mib_entity *e;
	unsigned int count = 0, i = 0;

	for (e = model->entities; e; e = e->next)
		if (config->shared ? e->class_id == 11 || e->class_id == 329 :
		    e->class_id == (config->veip ? 329 : 11) && e->instance == config->uni)
			count++;
	if (!count)
		return -ENOENT;
	/* Reserve one bridge number for the ANI and one for shared multicast. */
	if (count > 254U - !!config->multicast)
		return -ENOSPC;
	job->unis = calloc(count, sizeof(*job->unis));
	if (!job->unis)
		return -ENOMEM;
	job->uni_count = count;
	for (e = model->entities; e; e = e->next)
		if (config->shared ? e->class_id == 11 || e->class_id == 329 :
		    e->class_id == (config->veip ? 329 : 11) && e->instance == config->uni) {
			job->unis[i].instance = e->instance;
			job->unis[i++].veip = e->class_id == 329;
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
	struct fttr_ani_config ani = { .bridge = 0x8080 };
	struct fttr_gem_config g = { .profile = 0x8080, .gal = 1, .bridge = true };
	const struct fttr_mib_entity *e, *assigned = NULL;
	uint16_t alloc;
	uint32_t current;
	uint8_t ani_number = 0, mcast_number = 0;
	int i, ret;

	memset(job, 0, sizeof(*job));
	job->phase = "select";
	job->transaction.me.onu = onu;
	memcpy(job->transaction.serial, serial, 8);
	job->config = *config;
	if (config->multicast > 2 || (config->multicast && !config->shared))
		return job->error = -EINVAL;
	ret = fttr_miniolt_alloc_id(config->backend, onu, config->slot);
	if (ret < 0)
		return job->error = ret;
	alloc = ret;
	ret = select_unis(job, model);
	if (ret)
		return job->error = ret;
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
	ret = ports(job, model, &ani_number, &mcast_number);
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
	ani.entity = job->ani_port;
	ani.pointer = job->gem;
	ani.port = ani_number;
	ani.count = config->tagged ? 1 : 0;
	ani.vlans[0] = config->vid;
	fttr_ani_job_start(&association, onu, counter, &ani, serial);
	i = job->transaction.count;
	append(job, &association.transaction);
	job->objects[i].dependencies |= (1U << 2) | (1U << 4);
	for (i = 0; i < (int)job->uni_count; i++) {
		struct fttr_service_uni *u = &job->unis[i];
		struct fttr_uni_config uni = { .entity = u->port, .uni = u->instance,
			.bridge = 0x8080, .veip = u->veip };
		int count;

		fttr_uni_job_start(&association, onu, counter, &uni, serial);
		association.objects[0].create.value[2] = u->number;
		count = association.transaction.count;
		memcpy(u->objects, association.objects, count * sizeof(*u->objects));
		u->objects[count++] = (struct fttr_omci_object) {
			.class_id = u->veip ? 329 : 11, .instance = u->instance, .update_only = true,
			.create = { .mask = u->veip ? 0x8000 : 0x0800, .size = 1, .value = { 0 } }
		};
		fttr_objects_job_start(&u->transaction, onu, counter, u->objects, count, serial);
		u->transaction.defer_apply = true;
	}
	if (config->multicast) {
		/* Original default API chooses first VEIP when reported, else Ethernet. */
		job->mcast_uni = 0;
		for (i = 0; i < (int)job->uni_count; i++)
			if (job->unis[i].veip) { job->mcast_uni = i; break; }
		fttr_mcast_start(&job->mcast, onu, counter, job->unis[job->mcast_uni].port,
			job->mcast_port, mcast_number, config->multicast == 2, serial);
		job->mcast.defer_apply = true;
	}
	fttr_objects_job_start(&job->transaction, onu, counter, job->objects, job->transaction.count, serial);
	job->transaction.defer_apply = true;
	return 0;
}

static bool grant_equal(const struct fttr_tcont_grant *a, const struct fttr_tcont_grant *b)
{
	return a->fixed == b->fixed && a->limits == b->limits;
}

static int grant_write(struct fttr_service_job *job, int directory,
		       const struct fttr_onu_state *identity,
		       const struct fttr_tcont_grant *value, bool *sent)
{
	unsigned int onu = job->transaction.me.onu;
	int ret = fttr_miniolt_same(directory, onu, identity);

	if (!ret) {
		if (sent)
			*sent = true;
		ret = fttr_miniolt_tcont(directory, onu, job->config.slot,
			value->fixed, value->limits >> 16, value->limits & 0xffff);
	}
	if (!ret)
		ret = fttr_miniolt_tcont_read(directory, onu, job->config.slot, &job->observed);
	if (!ret && !grant_equal(value, &job->observed))
		ret = -EUCLEAN;
	if (!ret)
		ret = fttr_miniolt_same(directory, onu, identity);
	return ret;
}

static struct fttr_objects_job *pending_objects(struct fttr_service_job *job)
{
	if (job->stage == FTTR_SERVICE_OBJECTS || job->stage == FTTR_SERVICE_RELEASE ||
	    job->stage == FTTR_SERVICE_RELEASING_OBJECTS || job->stage == FTTR_SERVICE_CHECK)
		return &job->transaction;
	if (job->stage == FTTR_SERVICE_UNIS || job->stage == FTTR_SERVICE_ROLLBACK_UNIS ||
	    job->stage == FTTR_SERVICE_RELEASING_UNIS || job->stage == FTTR_SERVICE_CHECK_UNIS)
		return &job->unis[job->uni_index].transaction;
	return NULL;
}

struct fttr_me_job *fttr_service_pending(struct fttr_service_job *job)
{
	struct fttr_objects_job *objects = pending_objects(job);

	if (job->stage == FTTR_SERVICE_CHECK_MCAST || job->stage == FTTR_SERVICE_MCAST ||
	    job->stage == FTTR_SERVICE_RELEASE_MCAST)
		return fttr_mcast_pending(&job->mcast);
	if (objects)
		return &objects->me;
	if (job->stage == FTTR_SERVICE_VLAN || job->stage == FTTR_SERVICE_RELEASING_VLAN ||
	    job->stage == FTTR_SERVICE_ROLLBACK_VLAN)
		return fttr_vlan_change_pending(&job->unis[job->uni_index].vlan);
	return NULL;
}

void fttr_service_cancel(struct fttr_service_job *job, int error)
{
	struct fttr_me_job *me = fttr_service_pending(job);
	struct fttr_objects_job *objects = pending_objects(job);
	struct fttr_vlan_change *vlan;
	int original = job->error;
	bool releasing = job->stage == FTTR_SERVICE_RELEASE || job->stage == FTTR_SERVICE_RELEASING_OBJECTS ||
		job->stage == FTTR_SERVICE_RELEASING_VLAN || job->stage == FTTR_SERVICE_RELEASING_UNIS ||
		job->stage == FTTR_SERVICE_ROLLBACK_UNIS || job->stage == FTTR_SERVICE_ROLLBACK_VLAN;
	unsigned int i;

	if (job->stage == FTTR_SERVICE_CHECK_MCAST || job->stage == FTTR_SERVICE_MCAST ||
	    job->stage == FTTR_SERVICE_RELEASE_MCAST) {
		fttr_mcast_cancel(&job->mcast, error);
		job->error = job->mcast.error ? job->mcast.error : error;
		job->cleanup_error = job->mcast.cleanup_error;
		job->configured = job->grant_verified = false;
		job->stage = FTTR_SERVICE_FAILED;
		return;
	}
	vlan = me && !objects ? &job->unis[job->uni_index].vlan : NULL;

	if (!original && objects)
		original = objects->error;
	if (!original && vlan)
		original = vlan->error;
	if (me) {
		me->state = FTTR_ME_FAILED;
		me->error = error;
		if (objects) {
			if (objects->error || releasing) {
				objects->cleanup_error = error;
				objects->cleanup_failure = *me;
			} else {
				objects->error = error;
				objects->failure = *me;
			}
			objects->phase = FTTR_OBJECT_FAILED;
		} else {
			if (vlan->error || releasing) {
				vlan->cleanup_error = error;
				vlan->cleanup_failure = *me;
			} else {
				vlan->error = error;
				vlan->failure = *me;
			}
			vlan->stage = FTTR_VLAN_FAILED;
		}
	}
	if ((original || releasing) && me)
		job->cleanup_error = error;
	job->error = original ? original : error;
	job->stage = FTTR_SERVICE_FAILED;
	job->configured = job->grant_verified = false;
	job->mcast.configured = false;
	for (i = 0; i < job->uni_count; i++) {
		job->unis[i].vlan.verified = false;
		job->unis[i].vlan.after.table.complete = false;
	}
}

static int service_failed(struct fttr_service_job *job, int error)
{
	job->stage = FTTR_SERVICE_FAILED;
	job->error = error;
	return error;
}

int fttr_service_release_ready(const struct fttr_service_job *job)
{
	if (job->stage != FTTR_SERVICE_READY || job->error)
		return -EBUSY;
	/* Adoption proves current values, not authority to delete old resources.
	 * Without the original ownership journal, a move cannot safely reclaim
	 * the old T-CONT and GEMs or infer their prior configuration.
	 */
	if (!job->objects[0].touched || !job->objects[0].have_previous ||
	    !job->objects[3].touched || !job->objects[4].touched)
		return -ENODATA;
	return 0;
}

int fttr_service_release(struct fttr_service_job *job, uint16_t *counter)
{
	int ret = fttr_service_release_ready(job);

	if (ret || !job->grant_withdrawn)
		return ret ? ret : -EBUSY;
	if (job->config.multicast) {
		ret = fttr_mcast_release(&job->mcast, counter);
		if (ret < 0)
			return ret;
		job->configured = job->grant_verified = false;
		job->phase = "release-multicast";
		job->stage = FTTR_SERVICE_RELEASE_MCAST;
		return ret;
	}
	job->uni_index = job->uni_count - 1;
	ret = fttr_vlan_change_release(&job->unis[job->uni_index].vlan, counter);
	if (ret < 0)
		return ret;
	job->configured = job->grant_verified = false;
	job->phase = "release-vlan";
	job->stage = FTTR_SERVICE_RELEASING_VLAN;
	return ret;
}

static int release_objects(struct fttr_service_job *job, uint16_t *counter,
			   int directory, const struct fttr_onu_state *identity, int error)
{
	int ret;

	job->error = error;
	job->grant_verified = false;
	if (job->grant_sent) {
		job->cleanup_error = grant_write(job, directory, identity, &job->previous, NULL);
		if (job->cleanup_error)
			return service_failed(job, error);
		job->grant_restored = true;
	}
	job->uni_index = job->uni_count - 1;
	job->stage = FTTR_SERVICE_ROLLBACK_UNIS;
	ret = fttr_objects_job_release(&job->unis[job->uni_index].transaction, counter);
	/* Even an untouched UNI must advance through the release dispatcher. */
	return ret == 1 ? 1 : fttr_service_update(job, counter, directory, identity);
}

static int restore_vlans(struct fttr_service_job *job, uint16_t *counter,
		int directory, const struct fttr_onu_state *identity)
{
	int ret;

	if (job->uni_index < 0)
		return release_objects(job, counter, directory, identity, job->error);
	job->stage = FTTR_SERVICE_ROLLBACK_VLAN;
	ret = fttr_vlan_change_release(&job->unis[job->uni_index].vlan, counter);
	if (ret < 0) {
		job->cleanup_error = ret;
		return service_failed(job, job->error);
	}
	return 1;
}

int fttr_service_update(struct fttr_service_job *job, uint16_t *counter,
			int directory, const struct fttr_onu_state *identity)
{
	struct fttr_tcont_grant desired = { .fixed = job->config.fixed,
		.limits = (uint32_t)job->config.assured << 16 | job->config.maximum };
	struct fttr_vlan_plan plan;
	struct fttr_objects_job *objects;
	struct fttr_vlan_change *vlan;
	unsigned int onu = job->transaction.me.onu;
	int ret;

again:
	switch (job->stage) {
	case FTTR_SERVICE_READY: return 2;
	case FTTR_SERVICE_RELEASED: return 2;
	case FTTR_SERVICE_FAILED: return job->error;
	case FTTR_SERVICE_MCAST:
		ret = fttr_mcast_update(&job->mcast, counter);
		if (ret < 0) {
			job->error = ret;
			if (job->mcast.cleanup_error) {
				job->cleanup_error = job->mcast.cleanup_error;
				return service_failed(job, ret);
			}
			job->uni_index = job->uni_count - 1;
			return restore_vlans(job, counter, directory, identity);
		}
		if (ret != 2) return ret;
		job->configured = true;
		job->phase = "configured";
		job->stage = FTTR_SERVICE_READY;
		return 2;
	case FTTR_SERVICE_RELEASE_MCAST:
		ret = fttr_mcast_update(&job->mcast, counter);
		if (ret < 0) {
			job->cleanup_error = ret;
			return service_failed(job, ret);
		}
		if (ret != 2) return ret;
		job->uni_index = job->uni_count - 1;
		job->phase = "release-vlan";
		job->stage = FTTR_SERVICE_RELEASING_VLAN;
		ret = fttr_vlan_change_release(&job->unis[job->uni_index].vlan, counter);
		if (ret < 0) { job->cleanup_error = ret; return service_failed(job, ret); }
		return ret;
	case FTTR_SERVICE_ROLLBACK_VLAN:
	case FTTR_SERVICE_RELEASING_VLAN:
		vlan = &job->unis[job->uni_index].vlan;
		ret = fttr_vlan_change_update(vlan, counter);
		if (ret < 0) {
			job->cleanup_error = ret;
			return service_failed(job, job->error ? job->error : ret);
		}
		if (ret != 2)
			return ret;
		job->uni_index--;
		if (job->stage == FTTR_SERVICE_ROLLBACK_VLAN)
			return restore_vlans(job, counter, directory, identity);
		if (job->uni_index >= 0) {
			ret = fttr_vlan_change_release(&job->unis[job->uni_index].vlan, counter);
			if (ret < 0) {
				job->cleanup_error = ret;
				return service_failed(job, ret);
			}
			return 1;
		}
		job->uni_index = job->uni_count - 1;
		job->phase = "release-unis";
		job->stage = FTTR_SERVICE_RELEASING_UNIS;
		ret = fttr_objects_job_release_checked(&job->unis[job->uni_index].transaction, counter);
		if (ret < 0) {
			job->cleanup_error = ret;
			return service_failed(job, ret);
		}
		if (ret == 1)
			return 1;
		goto again;
	case FTTR_SERVICE_ROLLBACK_UNIS:
	case FTTR_SERVICE_RELEASING_UNIS:
		objects = &job->unis[job->uni_index].transaction;
		ret = fttr_objects_job_update(objects, counter);
		if (ret < 0 && (objects->cleanup_error || !job->error)) {
			job->cleanup_error = objects->cleanup_error ? objects->cleanup_error : ret;
			return service_failed(job, job->error ? job->error : ret);
		}
		if (ret >= 0 && ret != 2)
			return ret;
		if (--job->uni_index >= 0) {
			objects = &job->unis[job->uni_index].transaction;
			ret = job->stage == FTTR_SERVICE_RELEASING_UNIS ?
				fttr_objects_job_release_checked(objects, counter) : fttr_objects_job_release(objects, counter);
			if (ret < 0 && (objects->cleanup_error || !job->error)) {
				job->cleanup_error = ret;
				return service_failed(job, job->error ? job->error : ret);
			}
			if (ret == 1)
				return 1;
			goto again;
		}
		job->phase = "release-objects";
		if (job->stage == FTTR_SERVICE_ROLLBACK_UNIS) {
			job->stage = FTTR_SERVICE_RELEASE;
			ret = fttr_objects_job_release(&job->transaction, counter);
		} else {
			job->stage = FTTR_SERVICE_RELEASING_OBJECTS;
			ret = fttr_objects_job_release_checked(&job->transaction, counter);
		}
		if (ret < 0) {
			job->cleanup_error = ret;
			return service_failed(job, job->error ? job->error : ret);
		}
		if (ret == 1)
			return 1;
		goto again;
	case FTTR_SERVICE_RELEASING_OBJECTS:
		ret = fttr_objects_job_update(&job->transaction, counter);
		if (ret < 0) {
			job->cleanup_error = ret;
			return service_failed(job, ret);
		}
		if (ret != 2)
			return ret;
		job->phase = "released";
		job->stage = FTTR_SERVICE_RELEASED;
		return 2;
	case FTTR_SERVICE_START:
		job->phase = "grant-read";
		ret = fttr_miniolt_tcont_read(directory, onu, job->config.slot, &job->previous);
		if (ret)
			return service_failed(job, ret);
		job->observed = job->previous;
		if (job->previous.fixed > 0xffff)
			return service_failed(job, -EUCLEAN);
		if ((job->previous.fixed || job->previous.limits) &&
		    job->previous_alloc != fttr_miniolt_alloc_id(job->config.backend, onu, job->config.slot))
			return service_failed(job, -EBUSY);
		job->phase = "check-objects";
		job->stage = FTTR_SERVICE_CHECK;
		return 1;
	case FTTR_SERVICE_CHECK:
		ret = fttr_objects_job_update(&job->transaction, counter);
		if (ret < 0)
			return service_failed(job, ret);
		if (ret != 2)
			return ret;
		job->uni_index = 0;
		job->phase = "check-unis";
		job->stage = FTTR_SERVICE_CHECK_UNIS;
		return 1;
	case FTTR_SERVICE_CHECK_UNIS:
		objects = &job->unis[job->uni_index].transaction;
		ret = fttr_objects_job_update(objects, counter);
		if (ret < 0)
			return service_failed(job, ret);
		if (ret != 2)
			return ret;
		/* Cross-plan dependency: an existing UNI port cannot refer to a
		 * missing shared bridge. No configuration has been written yet.
		 */
		if (objects->objects[0].presence == FTTR_OBJECT_VERIFIED &&
		    job->objects[2].presence == FTTR_OBJECT_ABSENT)
			return service_failed(job, -EUCLEAN);
		if (++job->uni_index < (int)job->uni_count)
			return 1;
		if (job->config.multicast) {
			job->stage = FTTR_SERVICE_CHECK_MCAST;
			job->phase = "check-multicast";
			goto again;
		}
		goto apply_objects;
	case FTTR_SERVICE_CHECK_MCAST:
		ret = fttr_mcast_update(&job->mcast, counter);
		if (ret < 0) return service_failed(job, ret);
		if (ret != 2) return ret;
		/* Cross-plan references cannot adopt a missing GAL/bridge/UNI parent. */
		if ((job->mcast.objects[1].presence == FTTR_OBJECT_VERIFIED &&
		     job->objects[1].presence == FTTR_OBJECT_ABSENT) ||
		    (job->mcast.objects[5].presence == FTTR_OBJECT_VERIFIED &&
		     job->objects[2].presence == FTTR_OBJECT_ABSENT) ||
		    (job->mcast.objects[2].presence == FTTR_OBJECT_VERIFIED &&
		     job->unis[job->mcast_uni].objects[0].presence == FTTR_OBJECT_ABSENT))
			return service_failed(job, -EUCLEAN);
apply_objects:
		job->phase = "objects";
		job->stage = FTTR_SERVICE_OBJECTS;
		ret = fttr_objects_job_apply(&job->transaction, counter);
		if (ret == 1)
			return 1;
		goto again;
	case FTTR_SERVICE_OBJECTS:
	case FTTR_SERVICE_RELEASE:
		ret = fttr_objects_job_update(&job->transaction, counter);
		if (ret < 0) {
			job->cleanup_error = job->transaction.cleanup_error;
			return service_failed(job, job->error ? job->error : ret);
		}
		if (ret != FTTR_OBJECT_COMPLETE)
			return ret;
		if (job->stage == FTTR_SERVICE_RELEASE)
			return service_failed(job, job->error);
		job->uni_index = 0;
		job->stage = FTTR_SERVICE_UNIS;
		job->phase = "unis";
		ret = fttr_objects_job_apply(&job->unis[0].transaction, counter);
		if (ret == 1)
			return 1;
		goto again;
	case FTTR_SERVICE_UNIS:
		objects = &job->unis[job->uni_index].transaction;
		ret = fttr_objects_job_update(objects, counter);
		if (ret < 0) {
			if (objects->cleanup_error) {
				job->cleanup_error = objects->cleanup_error;
				return service_failed(job, ret);
			}
			return release_objects(job, counter, directory, identity, ret);
		}
		if (ret != 2)
			return ret;
		if (++job->uni_index < (int)job->uni_count) {
			ret = fttr_objects_job_apply(&job->unis[job->uni_index].transaction, counter);
			if (ret == 1)
				return 1;
			goto again;
		}
		job->uni_index = 0;
		job->phase = "grant";
		if (!grant_equal(&job->previous, &desired)) {
			ret = grant_write(job, directory, identity, &desired, &job->grant_sent);
			if (ret)
				return release_objects(job, counter, directory, identity, ret);
		}
		job->grant_verified = true;
		job->phase = "vlan";
		if (job->config.tagged) {
			struct fttr_vlan_plan untagged;

			/* Original access mode1 b4c10. */
			fttr_vlan_plan_build(&plan, FTTR_VLAN_DISCARD_TAGGED, 0, 0, 0, false);
			fttr_vlan_plan_build(&untagged, FTTR_VLAN_TAG_UNTAGGED,
				0, job->config.vid, job->config.priority, false);
			memcpy(plan.rows[plan.count++], untagged.rows[0], 16);
		} else {
			/* Original transparent mode4 b5704. */
			fttr_vlan_plan_build(&plan, FTTR_VLAN_FORWARD_ALL, 0, 0, 0, false);
		}
		fttr_vlan_change_start(&job->unis[0].vlan, onu, counter, job->unis[0].port, &plan);
		job->stage = FTTR_SERVICE_VLAN;
		return 1;
	case FTTR_SERVICE_VLAN:
		vlan = &job->unis[job->uni_index].vlan;
		ret = fttr_vlan_change_update(vlan, counter);
		if (ret < 0) {
			if (vlan->cleanup_error) {
				job->cleanup_error = vlan->cleanup_error;
				if (ret == -ESTALE || job->cleanup_error == -ESTALE)
					job->grant_verified = false;
				return service_failed(job, ret);
			}
			job->error = ret;
			job->uni_index--;
			return restore_vlans(job, counter, directory, identity);
		}
		if (ret != 2)
			return ret;
		if (++job->uni_index < (int)job->uni_count) {
			struct fttr_service_uni *uni = &job->unis[job->uni_index];

			fttr_vlan_change_start(&uni->vlan, onu, counter, uni->port, &job->unis[0].vlan.plan);
			return 1;
		}
		if (job->config.multicast) {
			job->stage = FTTR_SERVICE_MCAST;
			job->phase = "multicast";
			ret = fttr_mcast_apply(&job->mcast, counter);
			if (ret == 1) return ret;
			goto again;
		}
		job->configured = true;
		job->phase = "configured";
		job->stage = FTTR_SERVICE_READY;
		return 2;
	}
	return 0;
}

int fttr_service_execute(struct fttr_omci_session *session, struct fttr_service_job *job,
			 int directory, const struct fttr_onu_state *identity)
{
	int ret;

	while ((ret = fttr_service_update(job, &session->transaction, directory, identity)) == 1)
		fttr_me_exchange_same(session, fttr_service_pending(job), directory, identity);
	return ret < 0 ? ret : 0;
}

int fttr_service_dump(const struct fttr_service_job *job, FILE *file)
{
	int i, vlan_error = 0, vlan_cleanup_error = 0;

	for (i = 0; i < (int)job->uni_count; i++) {
		if (!vlan_error) vlan_error = job->unis[i].vlan.error;
		if (!vlan_cleanup_error) vlan_cleanup_error = job->unis[i].vlan.cleanup_error;
	}
	fprintf(file, "{\"event\":\"service_state\",\"id\":%u,\"serial\":\"", job->transaction.me.onu);
	for (i = 0; i < 8; i++)
		fprintf(file, "%02x", job->transaction.serial[i]);
	fprintf(file, "\",\"phase\":\"%s\",\"error\":%d,\"cleanup_error\":%d,"
		"\"shared\":%s,\"uni_count\":%u,\"configured\":%s,\"tcont\":%u,\"queue\":%u,\"gem\":%u,\"uni_port\":%u,\"ani_port\":%u,"
		"\"grant_sent\":%s,\"grant_verified\":%s,\"grant_restored\":%s,\"grant_withdrawn\":%s,"
		"\"previous_grant\":[%u,%u],\"observed_grant\":[%u,%u],\"vlan_error\":%d,\"vlan_cleanup_error\":%d}\n",
		job->phase, job->error, job->cleanup_error, job->config.shared ? "true" : "false", job->uni_count,
		job->configured ? "true" : "false", job->tcont, job->queue,
		job->gem, job->uni_port, job->ani_port, job->grant_sent ? "true" : "false",
		job->grant_verified ? "true" : "false", job->grant_restored ? "true" : "false",
		job->grant_withdrawn ? "true" : "false",
		job->previous.fixed, job->previous.limits, job->observed.fixed, job->observed.limits,
		vlan_error, vlan_cleanup_error);
	if (job->config.multicast)
		fttr_mcast_dump(&job->mcast, file);
	return ferror(file) ? -EIO : 0;
}

void fttr_service_clear(struct fttr_service_job *job)
{
	unsigned int i;

	for (i = 0; i < job->uni_count; i++) {
		free(job->unis[i].vlan.before.table.data);
		free(job->unis[i].vlan.after.table.data);
	}
	free(job->unis);
	job->unis = NULL;
	job->uni_count = 0;
}
