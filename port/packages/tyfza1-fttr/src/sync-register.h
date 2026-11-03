/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_SYNC_REGISTER_H
#define TYFZA1_SYNC_REGISTER_H

#include <json-c/json.h>
#include "sync-wire.h"

int fttr_sync_register_policy(struct json_object *policy);
/* Only compares self-reported identity against explicit pins; no crypto auth. */
int fttr_sync_register_reply(const struct fttr_sync_message *message,
	struct json_object *policy, const uint8_t source[6], struct json_object **reply);

#endif
