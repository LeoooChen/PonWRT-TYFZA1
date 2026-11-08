/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_MCAST_H
#define TYFZA1_FTTR_OMCI_MCAST_H

#include "omci-objects.h"

struct fttr_mcast_job {
	struct fttr_objects_job transaction;
	struct fttr_omci_object objects[7];
	struct fttr_me_job table, failure;
	uint32_t table_length;
	uint8_t table_row[12];
	int error, cleanup_error;
	bool prior_empty, table_sent, doubled, configured, releasing, defer_apply, recover_release;
	enum { FTTR_MCAST_CHECK, FTTR_MCAST_PRETABLE, FTTR_MCAST_APPLY,
		FTTR_MCAST_EMPTY, FTTR_MCAST_SET, FTTR_MCAST_READ, FTTR_MCAST_RELEASE_CHECK,
		FTTR_MCAST_READY, FTTR_MCAST_RELEASE_TABLE, FTTR_MCAST_DELETE_ROW,
		FTTR_MCAST_VERIFY_EMPTY, FTTR_MCAST_RELEASE, FTTR_MCAST_FAILED,
		FTTR_MCAST_RELEASED, FTTR_MCAST_CHECKED } stage;
};

/* GAL1, bridge8080 and the selected actual UNI bridge port already exist. */
void fttr_mcast_start(struct fttr_mcast_job *job, uint16_t onu, uint16_t *counter,
		uint16_t uni_port, uint16_t port, uint8_t number, bool extended,
		const uint8_t serial[8]);
int fttr_mcast_update(struct fttr_mcast_job *job, uint16_t *counter);
int fttr_mcast_apply(struct fttr_mcast_job *job, uint16_t *counter);
struct fttr_me_job *fttr_mcast_pending(struct fttr_mcast_job *job);
int fttr_mcast_release(struct fttr_mcast_job *job, uint16_t *counter);
void fttr_mcast_cancel(struct fttr_mcast_job *job, int error);
int fttr_mcast_dump(const struct fttr_mcast_job *job, FILE *file);

#endif
