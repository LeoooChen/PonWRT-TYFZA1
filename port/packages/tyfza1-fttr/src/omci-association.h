/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_ASSOCIATION_H
#define TYFZA1_FTTR_OMCI_ASSOCIATION_H

#include "omci-objects.h"

struct fttr_profiles_config {
	uint16_t bridge, mapper;
	bool per_uni, use_mapper;
};

struct fttr_uni_config {
	uint16_t entity, uni, bridge, ordinal;
	bool veip;
};

struct fttr_association_job {
	struct fttr_objects_job transaction;
	struct fttr_omci_object objects[3];
};

void fttr_profiles_job_start(struct fttr_association_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_profiles_config *config, const uint8_t serial[8]);
void fttr_uni_job_start(struct fttr_association_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_uni_config *config, const uint8_t serial[8]);

#endif
