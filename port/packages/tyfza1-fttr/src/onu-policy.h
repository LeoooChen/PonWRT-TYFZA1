/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_ONU_POLICY_H
#define TYFZA1_FTTR_ONU_POLICY_H
#include "miniolt.h"
#include <json-c/json.h>

struct fttr_onu_credentials {
	uint8_t serial[8], ploam[10];
	char loid[25], key[13];
	bool ploam_valid, loid_valid;
};

struct fttr_onu_policy_row {
	struct fttr_onu_credentials expected;
	unsigned int method;
	bool present, active, learned, bound;
};

struct fttr_onu_policy {
	struct fttr_onu_policy_row rows[FTTR_ONUS];
	uint8_t vendors[32][4];
	unsigned int vendor_count;
	bool enabled, auto_learn, auto_find;
	int directory;
	char name[40];
	struct json_object *document, *bindings;
};

enum fttr_onu_decision { FTTR_ONU_MATCH, FTTR_ONU_WAIT, FTTR_ONU_REJECT,
	FTTR_ONU_MOVE, FTTR_ONU_BIND, FTTR_ONU_LEARN };
struct fttr_onu_selection { enum fttr_onu_decision action; unsigned int id, method; int error; };

/* Comparator returns original0 exact,1 bind,2 reject, or a missing-input error. */
int fttr_onu_compare(unsigned int method, const struct fttr_onu_credentials *expected,
		const struct fttr_onu_credentials *observed);
int fttr_onu_policy_load(const char *path, struct fttr_onu_policy *policy);
void fttr_onu_policy_close(struct fttr_onu_policy *policy);
bool fttr_onu_policy_needs_loid(const struct fttr_onu_policy *policy);
struct fttr_onu_selection fttr_onu_policy_select(const struct fttr_onu_policy *policy,
		unsigned int hardware, const struct fttr_onu_credentials *observed, const bool live[FTTR_ONUS]);
/* Persist before allowing a bound/learned identity to retry authentication. */
int fttr_onu_policy_bind(struct fttr_onu_policy *policy, const struct fttr_onu_selection *selection,
		const uint8_t serial[8]);
#endif
