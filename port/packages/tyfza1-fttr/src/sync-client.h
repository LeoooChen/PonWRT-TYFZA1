/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_SYNC_CLIENT_H
#define TYFZA1_SYNC_CLIENT_H

#include <stdbool.h>
#include <json-c/json.h>
#include "sync-wire.h"

int fttr_sync_mac_parse(const char *text, uint8_t mac[6]);
int fttr_sync_reply_decode(const struct fttr_sync_message *message, const uint8_t peer[6],
		bool settings, bool writing, bool *ack_seen,
		struct json_object **summary, struct json_object **observed);
int fttr_sync_request(int fd, const uint8_t source[6], const uint8_t peer[6], uint16_t ethertype,
		uint8_t slot, bool settings, struct json_object *configuration);

#endif
