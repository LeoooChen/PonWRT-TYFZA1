/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_VLAN_H
#define TYFZA1_FTTR_OMCI_VLAN_H

#include "omci-me.h"

enum fttr_vlan_policy {
	FTTR_VLAN_DISCARD_ALL, FTTR_VLAN_FORWARD_ALL, FTTR_VLAN_DISCARD_UNTAGGED,
	FTTR_VLAN_DISCARD_TAGGED, FTTR_VLAN_TRANSLATE, FTTR_VLAN_TAG_UNTAGGED
};

/* Original policies produce at most three 16-byte class171 rows. */
struct fttr_vlan_plan { uint8_t count, rows[3][16]; };
int fttr_vlan_plan_build(struct fttr_vlan_plan *plan, enum fttr_vlan_policy policy,
			uint16_t input_vid, uint16_t output_vid, uint8_t priority, bool remove);

struct fttr_vlan_table {
	uint16_t capacity;
	uint32_t length, received;
	uint8_t *data;
	bool complete;
};

enum fttr_vlan_read_phase { FTTR_VLAN_CAPACITY, FTTR_VLAN_LENGTH,
	FTTR_VLAN_NEXT, FTTR_VLAN_READ_DONE, FTTR_VLAN_READ_FAILED };
struct fttr_vlan_read_job {
	struct fttr_me_job me;
	struct fttr_vlan_table table;
	enum fttr_vlan_read_phase phase;
	int error;
};

void fttr_vlan_read_start(struct fttr_vlan_read_job *job, uint16_t onu,
			 uint16_t *counter, uint16_t instance);
/* One requests IO, zero completes, negative fails. Caller frees table.data. */
int fttr_vlan_read_update(struct fttr_vlan_read_job *job, uint16_t *counter);
int fttr_vlan_read(struct fttr_omci_session *session, uint16_t instance, int directory,
		   const struct fttr_onu_state *identity, struct fttr_vlan_read_job *job);

struct fttr_vlan_change {
	struct fttr_vlan_read_job before, after;
	struct fttr_me_job me, failure, cleanup_failure;
	const char *phase;
	int error, cleanup_error;
	uint8_t sent;
	bool verified, restored;
	struct fttr_vlan_plan plan;
	enum { FTTR_VLAN_READ, FTTR_VLAN_SET, FTTR_VLAN_VERIFY, FTTR_VLAN_RESTORE_READ,
		FTTR_VLAN_RESTORE, FTTR_VLAN_RESTORE_VERIFY, FTTR_VLAN_DONE, FTTR_VLAN_FAILED } stage;
	uint16_t onu, instance;
	int index;
};

void fttr_vlan_change_start(struct fttr_vlan_change *change, uint16_t onu,
		uint16_t *counter, uint16_t instance, const struct fttr_vlan_plan *plan);
/* Zero waits, one requests IO, two completes, negative fails. */
int fttr_vlan_change_update(struct fttr_vlan_change *change, uint16_t *counter);
struct fttr_me_job *fttr_vlan_change_pending(struct fttr_vlan_change *change);
int fttr_vlan_apply(struct fttr_omci_session *session, uint16_t instance,
		   const struct fttr_vlan_plan *plan, int directory,
		   const struct fttr_onu_state *identity, struct fttr_vlan_change *change);

#endif
