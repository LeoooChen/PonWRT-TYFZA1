/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_SERVICE_H
#define TYFZA1_FTTR_OMCI_SERVICE_H

#include "omci-model.h"
#include "omci-association.h"
#include "omci-gem.h"
#include "omci-vlan.h"

struct fttr_service_config {
	uint16_t uni, slot, priority, vid, fixed, assured, maximum;
	bool veip, tagged;
};

struct fttr_service_job {
	struct fttr_objects_job transaction;
	struct fttr_omci_object objects[11];
	struct fttr_vlan_change vlan;
	struct fttr_tcont_grant previous, observed;
	struct fttr_service_config config;
	uint16_t tcont, queue, gem, uni_port, ani_port, previous_alloc;
	const char *phase;
	int error, cleanup_error;
	bool grant_sent, grant_verified, grant_restored, configured;
};

/* Select real MEs and build the original direct-bridge service dependency graph. */
int fttr_service_start(struct fttr_service_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_mib_model *model, const struct fttr_service_config *config,
		const uint8_t serial[8]);
int fttr_service_execute(struct fttr_omci_session *session, struct fttr_service_job *job,
			 int directory, const struct fttr_onu_state *identity);
int fttr_service_dump(const struct fttr_service_job *job, FILE *file);
void fttr_service_clear(struct fttr_service_job *job);

#endif
