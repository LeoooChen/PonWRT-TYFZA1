/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_SYNC_CONTROLLER_H
#define TYFZA1_SYNC_CONTROLLER_H

#include <stdint.h>
#include <json-c/json.h>

int fttr_sync_controller_policy(struct json_object *policy);
int fttr_sync_controller(int fd, const uint8_t source[6], uint16_t ethertype,
		struct json_object *policy, const char *state_directory);

#endif
