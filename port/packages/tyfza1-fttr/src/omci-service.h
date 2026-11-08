/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_SERVICE_H
#define TYFZA1_FTTR_OMCI_SERVICE_H

#include "omci-model.h"
#include "omci-association.h"
#include "omci-gem.h"
#include "omci-vlan.h"
#include "omci-mcast.h"

struct fttr_service_config {
	uint16_t uni, slot, priority, vid, fixed, assured, maximum;
	bool veip, tagged, shared, filter_only, factory;
	uint8_t multicast; /* 0 disabled, 1 original standard, 2 original CTC profile */
	enum fttr_backend backend;
};

/* Original onuinfo defaults: total includes management slot0. */
int fttr_service_default(struct fttr_service_config *config, unsigned int total, unsigned int slot);

/* none: transparent; VID: access; filter:VID: transparent UNI/ANI filter. */
int fttr_service_vlan_parse(const char *text, bool *tagged, bool *filter_only, uint16_t *vid);

struct fttr_service_uni {
	uint16_t instance, port;
	uint8_t number;
	bool veip;
	struct fttr_objects_job transaction;
	struct fttr_omci_object objects[4];
	struct fttr_vlan_change vlan;
	const struct fttr_vlan_table *shared_table;
};

struct fttr_service_job {
	struct fttr_objects_job transaction;
	struct fttr_omci_object objects[7];
	struct fttr_service_uni *unis;
	struct fttr_mcast_job mcast;
	unsigned int uni_count;
	int uni_index;
	struct fttr_tcont_grant previous, observed;
	struct fttr_service_config config;
	uint16_t tcont, queue, gem, uni_port, ani_port, previous_alloc, mcast_port, mcast_uni;
	const char *phase;
	int error, cleanup_error;
	bool grant_sent, grant_verified, grant_restored, grant_withdrawn, configured;
	bool recover_apply; /* Only a durable initial-write ledger grants partial cleanup rights. */
	bool require_shared; /* A later collection member must reuse its live parents. */
	enum { FTTR_SERVICE_START, FTTR_SERVICE_OBJECTS, FTTR_SERVICE_VLAN,
		FTTR_SERVICE_RELEASE, FTTR_SERVICE_READY, FTTR_SERVICE_FAILED,
		FTTR_SERVICE_RELEASING_VLAN, FTTR_SERVICE_RELEASING_OBJECTS, FTTR_SERVICE_RELEASED,
		FTTR_SERVICE_UNIS, FTTR_SERVICE_ROLLBACK_VLAN, FTTR_SERVICE_ROLLBACK_UNIS,
		FTTR_SERVICE_RELEASING_UNIS, FTTR_SERVICE_CHECK, FTTR_SERVICE_CHECK_UNIS,
		FTTR_SERVICE_CHECK_MCAST, FTTR_SERVICE_MCAST, FTTR_SERVICE_RELEASE_MCAST } stage;
};

/* Select real MEs for one UNI, explicit shared inventory, or the factory card limits. */
int fttr_service_start(struct fttr_service_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_mib_model *model, const struct fttr_service_config *config,
		const uint8_t serial[8]);
/* Zero waits, one requests IO, two completes, negative fails. */
int fttr_service_update(struct fttr_service_job *job, uint16_t *counter,
			int directory, const struct fttr_onu_state *identity);
struct fttr_me_job *fttr_service_pending(struct fttr_service_job *job);
void fttr_service_cancel(struct fttr_service_job *job, int error);
/* Requires an intact current-process ownership recipe and withdrawn local DBA. */
int fttr_service_release(struct fttr_service_job *job, uint16_t *counter);
int fttr_service_release_ready(const struct fttr_service_job *job);
/* Batch failure restores only this job's writes, including its prior DBA. */
int fttr_service_rollback(struct fttr_service_job *job, uint16_t *counter,
			 int directory, const struct fttr_onu_state *identity);
int fttr_service_execute(struct fttr_omci_session *session, struct fttr_service_job *job,
			 int directory, const struct fttr_onu_state *identity);
int fttr_service_dump(const struct fttr_service_job *job, FILE *file);
void fttr_service_clear(struct fttr_service_job *job);

#endif
