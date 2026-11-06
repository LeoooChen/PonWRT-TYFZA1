// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <string.h>
#include "omci-services.h"

static int project(struct fttr_mib_model *model, const struct fttr_objects_job *objects)
{
	int i, group, ret;
	for (i = 0; i < objects->count; i++)
		for (group = 0; group < 2; group++) {
			const struct fttr_omci_object *o = &objects->objects[i];
			const struct fttr_omci_values *v = group ? &o->set : &o->create;
			if (!v->mask) continue;
			ret = fttr_mib_model_project(model, o->class_id, o->instance, v->mask, v->value, v->size);
			if (ret) return ret;
		}
	return 0;
}

int fttr_services_start(struct fttr_services *set, uint16_t onu, uint16_t *counter,
	const struct fttr_mib_model *model, const struct fttr_service_config *configs,
	unsigned int count, const uint8_t serial[8])
{
	struct fttr_mib_model reserved;
	unsigned int i, j, mc = 0;
	int ret = -EINVAL;

	memset(set, 0, sizeof(*set));
	if (!count || count > FTTR_SERVICES) goto fail;
	/* Original multi-flow defaults share transparent UNIs and select distinct
	 * ANI VLANs. Reject conflicting UNI edits before any OMCI or DBA write. */
	for (i = 0; i < count; i++) {
		if (count > 1 && (!configs[i].shared || !configs[i].filter_only)) goto fail;
		if (configs[i].multicast && ++mc > 1) goto fail;
		for (j = 0; j < i; j++)
			if (configs[i].slot == configs[j].slot || configs[i].vid == configs[j].vid) goto fail;
	}
	ret = fttr_mib_model_copy(&reserved, model);
	if (ret) goto fail;
	for (i = 0; i < count; i++) {
		struct fttr_service_job *job = &set->job[i];
		set->count++;
		ret = fttr_service_start(job, onu, counter, &reserved, &configs[i], serial);
		if (ret) break;
		job->require_shared = i != 0;
		if (i + 1 == count) break;
		/* Reservations exist only in this private planning snapshot. Each
		 * job still probes the real remote objects before it can write. */
		ret = project(&reserved, &job->transaction);
		for (j = 0; !ret && j < job->uni_count; j++)
			ret = project(&reserved, &job->unis[j].transaction);
		if (!ret && job->config.multicast) ret = project(&reserved, &job->mcast.transaction);
		if (ret) break;
	}
	fttr_mib_model_clear(&reserved);
	if (!ret) return 0;
fail:
	set->error = ret;
	set->stage = FTTR_SERVICES_FAILED;
	return ret;
}

struct fttr_me_job *fttr_services_pending(struct fttr_services *set)
{
	return set->count && set->index < set->count ? fttr_service_pending(&set->job[set->index]) : NULL;
}

static int failed(struct fttr_services *set, int cleanup)
{
	unsigned int i;
	set->configured = false;
	for (i = 0; i < set->count; i++) set->job[i].configured = false;
	if (cleanup) set->cleanup_error = cleanup;
	set->stage = FTTR_SERVICES_FAILED;
	return set->error ? set->error : cleanup;
}

int fttr_services_update(struct fttr_services *set, uint16_t *counter, int directory,
	const struct fttr_onu_state *identity)
{
	int ret;
	for (;;) {
		struct fttr_service_job *job = &set->job[set->index];
		if (set->stage == FTTR_SERVICES_FAILED) return set->error ? set->error : set->cleanup_error;
		if (set->stage == FTTR_SERVICES_READY || set->stage == FTTR_SERVICES_RELEASED) return 2;
		ret = fttr_service_update(job, counter, directory, identity);
		if (ret == 0 || ret == 1) return ret;
		if (ret < 0) {
			if (set->stage != FTTR_SERVICES_APPLY) return failed(set, ret);
			set->error = ret;
			if (job->cleanup_error || !set->index) return failed(set, job->cleanup_error);
			set->stage = FTTR_SERVICES_ROLLBACK;
		} else if (set->stage == FTTR_SERVICES_APPLY) {
			if (++set->index < set->count) {
				struct fttr_service_job *next = &set->job[set->index];
				/* Plans have the same complete UNI inventory and ordering.
				 * Keep the prior job alive until its successors retire. */
				for (unsigned int i = 0; i < next->uni_count; i++)
					next->unis[i].shared_table = &job->unis[i].vlan.after.table;
				continue;
			}
			set->index = 0;
			set->configured = true;
			set->stage = FTTR_SERVICES_READY;
			return 2;
		} else if (!set->index) {
			if (set->stage == FTTR_SERVICES_ROLLBACK) return failed(set, 0);
			set->stage = FTTR_SERVICES_RELEASED;
			return 2;
		}
		job = &set->job[--set->index];
		ret = set->stage == FTTR_SERVICES_ROLLBACK ?
			fttr_service_rollback(job, counter, directory, identity) : fttr_service_release(job, counter);
		if (ret < 0) return failed(set, ret);
		if (ret == 1) return 1;
	}
}

int fttr_services_release_ready(const struct fttr_services *set)
{
	unsigned int i;
	int ret;
	if (set->stage != FTTR_SERVICES_READY || set->error) return -EBUSY;
	for (i = 0; i < set->count; i++) {
		ret = fttr_service_release_ready(&set->job[i]);
		if (ret) return ret;
	}
	return 0;
}

int fttr_services_release(struct fttr_services *set, uint16_t *counter)
{
	unsigned int i;
	int ret = fttr_services_release_ready(set);
	if (ret) return ret;
	for (i = 0; i < set->count; i++) if (!set->job[i].grant_withdrawn) return -EBUSY;
	set->index = set->count - 1;
	set->configured = false;
	set->stage = FTTR_SERVICES_RELEASE;
	return fttr_service_release(&set->job[set->index], counter);
}

void fttr_services_cancel(struct fttr_services *set, int error)
{
	unsigned int i;
	for (i = 0; i < set->count; i++)
		if (set->job[i].stage != FTTR_SERVICE_RELEASED) fttr_service_cancel(&set->job[i], error);
	if (!set->error) set->error = error;
	set->configured = false;
	set->stage = FTTR_SERVICES_FAILED;
}

void fttr_services_clear(struct fttr_services *set)
{
	unsigned int i;
	for (i = 0; i < set->count; i++) fttr_service_clear(&set->job[i]);
	memset(set, 0, sizeof(*set));
}
