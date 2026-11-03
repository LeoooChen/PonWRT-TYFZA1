/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_SYNC_STORE_H
#define TYFZA1_SYNC_STORE_H

#include <stdint.h>
#include <json-c/json.h>

/* The directory must already exist with private permissions. Returned fd
 * owns an exclusive filesystem lease until closed by the controller. */
int fttr_sync_store_open(const char *directory);
int fttr_sync_store_revision(struct json_object *peer);
int fttr_sync_store_load(int directory, const uint8_t source[6], const uint8_t peer[6],
	uint16_t ethertype, struct json_object *policy, struct json_object **record,
	unsigned int *attempted);
/* Called and fsynced before transmission; error means no write may be sent. */
int fttr_sync_store_mark(int directory, const uint8_t peer[6], struct json_object *record,
	unsigned int attempted);

#endif
