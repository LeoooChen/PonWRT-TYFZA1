/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_SYNC_WIFI_H
#define TYFZA1_SYNC_WIFI_H

#include <stdbool.h>
#include <json-c/json.h>

/* Explicit full Flag1 content; ENCRYPT uses the original wire/MIB enum. */
int fttr_sync_wifi_load(const char *path, struct json_object **desired);
int fttr_sync_wifi_validate(struct json_object *desired);
/* Validate the observed device-local index/band and compare all fields the
 * retained manager can read. PWD is absent from that producer, never verified. */
int fttr_sync_wifi_check(struct json_object *entries, struct json_object *desired,
			bool compare);

int fttr_sync_radio_validate(struct json_object *desired);
int fttr_sync_radio_check(struct json_object *entries, struct json_object *desired, bool compare);
#endif
