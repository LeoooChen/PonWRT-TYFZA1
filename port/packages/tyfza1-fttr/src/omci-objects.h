/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_OBJECTS_H
#define TYFZA1_FTTR_OMCI_OBJECTS_H

#include <stdio.h>
#include "miniolt.h"
#include "omci-me.h"

struct fttr_omci_values {
	uint16_t mask;
	uint8_t size, value[29]; /* Baseline Get attribute budget. */
};

struct fttr_omci_object {
	uint16_t class_id, instance;
	struct fttr_omci_values create, set;
	uint32_t dependencies; /* Earlier prerequisites in this fixed plan. */
	enum { FTTR_OBJECT_UNKNOWN, FTTR_OBJECT_ABSENT, FTTR_OBJECT_PRESENT, FTTR_OBJECT_VERIFIED } presence;
	bool touched;
};

enum fttr_objects_phase {
	FTTR_OBJECT_CHECK, FTTR_OBJECT_CREATE, FTTR_OBJECT_CONFIGURE, FTTR_OBJECT_VERIFY,
	FTTR_OBJECT_CLEANUP_PROBE, FTTR_OBJECT_DELETE, FTTR_OBJECT_DELETE_VERIFY,
	FTTR_OBJECT_CONFIGURED, FTTR_OBJECT_RELEASED, FTTR_OBJECT_FAILED
};
enum fttr_objects_action { FTTR_OBJECT_REQUEST = 1, FTTR_OBJECT_COMPLETE = 2 };

struct fttr_objects_job {
	struct fttr_me_job me, failure, cleanup_failure;
	struct fttr_omci_object *objects;
	uint8_t serial[8];
	enum fttr_objects_phase phase;
	int count, index, group, error, cleanup_error;
};

/* The caller owns the fixed, ordered plan (at most 32 objects) for the job lifetime. */
void fttr_objects_job_start(struct fttr_objects_job *job, uint16_t onu, uint16_t *counter,
		struct fttr_omci_object *objects, int count, const uint8_t serial[8]);
int fttr_objects_job_update(struct fttr_objects_job *job, uint16_t *counter);
/* Release created resources while preserving objects reused from the initial state. */
int fttr_objects_job_release(struct fttr_objects_job *job, uint16_t *counter);
int fttr_objects_job_dump(const struct fttr_objects_job *job, FILE *file, const char *event);
int fttr_objects_execute(struct fttr_omci_session *session, struct fttr_objects_job *job,
			int directory, const struct fttr_onu_state *identity);

#endif
