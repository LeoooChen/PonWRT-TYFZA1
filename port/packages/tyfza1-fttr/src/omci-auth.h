/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_AUTH_H
#define TYFZA1_FTTR_OMCI_AUTH_H

#include "omci-me.h"

/* Original global FTTR LOID policy, independent of upstream PON identity. */
struct fttr_auth_config {
	char loid[25], key[13];
	bool enabled;
};

enum fttr_auth_phase { FTTR_AUTH_DISABLED, FTTR_AUTH_CTC_LOID, FTTR_AUTH_CTC_KEY,
	FTTR_AUTH_ONUG_LOID, FTTR_AUTH_ONUG_KEY, FTTR_AUTH_WRITE, FTTR_AUTH_VERIFY,
	FTTR_AUTH_DONE, FTTR_AUTH_FAILED };

struct fttr_auth_job {
	struct fttr_me_job me;
	enum fttr_auth_phase phase;
	char loid[25];
	uint8_t result;
	bool acquired, verified;
	int error, fallback_error;
};

/* Private factory file: TFL1, LOID[25], key[13], both NUL terminated.
 * Empty strings disable checking, matching the original startup predicate.
 */
int fttr_auth_config_read(const char *path, struct fttr_auth_config *config);
int fttr_auth_start(struct fttr_auth_job *job, const struct fttr_auth_config *config,
		uint16_t onu, uint16_t *counter);
/* Advance a completed/failed ME. Positive means a new request, zero terminal. */
int fttr_auth_advance(struct fttr_auth_job *job, const struct fttr_auth_config *config,
		uint16_t *counter);
void fttr_auth_cancel(struct fttr_auth_job *job, int error);

#endif
