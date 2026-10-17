/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_GEM_H
#define TYFZA1_FTTR_OMCI_GEM_H

#include "omci-objects.h"

struct fttr_gem_config {
	uint16_t entity, tcont, queue, profile, gal, descriptor;
	uint32_t committed, peak; /* Descriptor rates in wire bytes/second. */
	bool bridge; /* Original association flag: true -> option1; false -> option5. */
};

struct fttr_gem_job {
	struct fttr_objects_job transaction;
	struct fttr_omci_object objects[3];
};

void fttr_gem_job_start(struct fttr_gem_job *job, uint16_t onu, uint16_t *counter,
			const struct fttr_gem_config *config, const uint8_t serial[8]);

#endif
