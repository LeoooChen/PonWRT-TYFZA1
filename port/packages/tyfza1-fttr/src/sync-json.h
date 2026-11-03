/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_SYNC_JSON_H
#define TYFZA1_SYNC_JSON_H

#include <json-c/json.h>

int fttr_sync_json_load_private(const char *path, struct json_object **value);

#endif
