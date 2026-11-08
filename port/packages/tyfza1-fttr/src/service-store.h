/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_SERVICE_STORE_H
#define TYFZA1_FTTR_SERVICE_STORE_H
#include "omci-services.h"

/* A committed record owns remote objects, never live local DMA/FE resources. */
int fttr_service_store_open(const char *path);
/* Inspect private intent headers before a bootstrap could reset their hardware. */
int fttr_service_store_interrupted(int directory);
int fttr_service_store_save(int directory, const struct fttr_omci_link *link,
	unsigned int onu, const uint8_t serial[8], enum fttr_backend backend,
	const struct fttr_services *set);
/* Load: 0 committed, 1 interrupted release, 2 initial apply intent; negative error. */
int fttr_service_store_load(int directory, const struct fttr_omci_link *link,
	unsigned int onu, const uint8_t serial[8], enum fttr_backend backend,
	struct fttr_services *set);
bool fttr_service_store_reset(const struct fttr_services *saved, const struct fttr_mib_model *model);
int fttr_service_store_match(const struct fttr_services *saved, const struct fttr_services *planned);
/* Persist before the first remote mutation, and before releasing a ready set. */
int fttr_service_store_checkpoint(int directory, const struct fttr_omci_link *link,
	unsigned int onu, const uint8_t serial[8], enum fttr_backend backend,
	const struct fttr_services *set);
int fttr_service_store_pending(int directory, const struct fttr_omci_link *link,
	unsigned int onu, const uint8_t serial[8], enum fttr_backend backend,
	const struct fttr_services *committed);
int fttr_service_store_remove(int directory, const uint8_t serial[8]);

struct fttr_service_recovery {
	struct fttr_me_job me;
	struct fttr_vlan_read_job vlan;
	unsigned int service, uni, object, group;
	enum { FTTR_REC_SYNC_BEFORE, FTTR_REC_OBJECTS, FTTR_REC_UNIS, FTTR_REC_VLAN,
		FTTR_REC_MCAST, FTTR_REC_TABLE, FTTR_REC_ROW, FTTR_REC_SYNC_AFTER, FTTR_REC_DONE } stage;
	bool started;
	uint8_t sync;
};
/* Checks all saved attributes and full VLAN tables without remote writes. */
int fttr_service_recovery_update(struct fttr_service_recovery *r,
	struct fttr_services *set, uint16_t *counter);
struct fttr_me_job *fttr_service_recovery_pending(struct fttr_service_recovery *r);
void fttr_service_recovery_clear(struct fttr_service_recovery *r);
#endif
