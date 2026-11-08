/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_SERVICES_H
#define TYFZA1_FTTR_OMCI_SERVICES_H
#include "omci-service.h"

#define FTTR_SERVICES 7
struct fttr_services {
	struct fttr_service_job job[FTTR_SERVICES];
	unsigned int count, index;
	int error, cleanup_error;
	bool configured;
	enum { FTTR_SERVICES_APPLY, FTTR_SERVICES_READY, FTTR_SERVICES_ROLLBACK,
		FTTR_SERVICES_RELEASE, FTTR_SERVICES_RELEASED, FTTR_SERVICES_FAILED } stage;
};
int fttr_services_start(struct fttr_services *set, uint16_t onu, uint16_t *counter,
	const struct fttr_mib_model *model, const struct fttr_service_config *configs,
	unsigned int count, const uint8_t serial[8]);
struct fttr_me_job *fttr_services_pending(struct fttr_services *set);
int fttr_services_update(struct fttr_services *set, uint16_t *counter, int directory,
	const struct fttr_onu_state *identity);
int fttr_services_release_ready(const struct fttr_services *set);
int fttr_services_recover_release(struct fttr_services *set, uint16_t *counter);
int fttr_services_release(struct fttr_services *set, uint16_t *counter);
void fttr_services_cancel(struct fttr_services *set, int error);
void fttr_services_clear(struct fttr_services *set);
#endif
