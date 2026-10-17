/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_GEM_H
#define TYFZA1_FTTR_OMCI_GEM_H

#include <stdio.h>
#include "miniolt.h"
#include "omci-me.h"

struct fttr_gem_config {
	uint16_t entity, tcont, queue, profile, gal, descriptor;
	uint32_t committed, peak; /* Descriptor rates in wire bytes/second. */
	bool bridge; /* Original association flag: true -> option1; false -> option5. */
};

enum fttr_gem_phase {
	FTTR_GEM_CHECK, FTTR_GEM_CREATE, FTTR_GEM_VERIFY,
	FTTR_GEM_CLEANUP_PROBE, FTTR_GEM_DELETE, FTTR_GEM_DELETE_VERIFY,
	FTTR_GEM_CONFIGURED, FTTR_GEM_RELEASED, FTTR_GEM_FAILED
};
enum fttr_gem_presence { FTTR_GEM_UNKNOWN, FTTR_GEM_ABSENT, FTTR_GEM_PRESENT, FTTR_GEM_VERIFIED };
enum fttr_gem_action { FTTR_GEM_REQUEST = 1, FTTR_GEM_COMPLETE = 2 };

struct fttr_gem_object {
	uint16_t class_id, instance, mask;
	uint8_t size, value[20];
	enum fttr_gem_presence presence;
	bool touched; /* Absent before this operation, then Create was transmitted. */
};

struct fttr_gem_job {
	struct fttr_me_job me, failure, cleanup_failure;
	struct fttr_gem_object objects[3];
	uint8_t serial[8];
	enum fttr_gem_phase phase;
	int count, index, error, cleanup_error;
};

void fttr_gem_job_start(struct fttr_gem_job *job, uint16_t onu, uint16_t *counter,
			const struct fttr_gem_config *config, const uint8_t serial[8]);
/* Call after sending, timeout handling or a matching ME reply updates job->me. */
int fttr_gem_job_update(struct fttr_gem_job *job, uint16_t *counter);
/* Release only objects created by this configured job; preserve reused objects. */
int fttr_gem_job_release(struct fttr_gem_job *job, uint16_t *counter);
int fttr_gem_job_dump(const struct fttr_gem_job *job, FILE *file);
int fttr_gem_execute(struct fttr_omci_session *session, struct fttr_gem_job *job,
		     int directory, const struct fttr_onu_state *identity);

#endif
