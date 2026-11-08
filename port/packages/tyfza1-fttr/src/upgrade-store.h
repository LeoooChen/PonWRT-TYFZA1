/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_UPGRADE_STORE_H
#define TYFZA1_FTTR_UPGRADE_STORE_H
#include "omci-upgrade.h"
struct fttr_upgrade_record {
	uint8_t serial[8], host[6], olt[6], version[14];
	unsigned int backend, action, image, stage;
	uint32_t size, crc;
	bool pending;
};
int fttr_upgrade_store_open(const char *path);
int fttr_upgrade_store_load(int directory, const uint8_t serial[8], struct fttr_upgrade_record *record);
int fttr_upgrade_store_save(int directory, const struct fttr_upgrade_record *record);
int fttr_upgrade_store_remove(int directory, const uint8_t serial[8]);
#endif
