/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_MINIOLT_H
#define TYFZA1_FTTR_MINIOLT_H

#include "omci-wire.h"

#define FTTR_ONUS 64

struct fttr_onu_state {
	uint32_t downstream, upstream, eqd;
	uint8_t serial[8];
};

int fttr_miniolt_table(int directory, struct fttr_onu_state table[FTTR_ONUS]);
int fttr_miniolt_link(int directory, struct fttr_omci_link *link);
int fttr_miniolt_links_ready(int directory, int ni);
int fttr_miniolt_one(int directory, unsigned int id, struct fttr_onu_state *onu);
int fttr_miniolt_same(int directory, unsigned int id, const struct fttr_onu_state *expected);
int fttr_miniolt_management(int directory, unsigned int id, bool enable);
uint16_t fttr_miniolt_alloc_id(unsigned int id, unsigned int slot);
uint16_t fttr_miniolt_gem_id(unsigned int id, unsigned int ordinal);
int fttr_miniolt_tcont(int directory, unsigned int id, unsigned int slot,
		       unsigned int fixed, unsigned int assured, unsigned int maximum);
struct fttr_tcont_grant { uint32_t fixed, limits; };
int fttr_miniolt_tcont_read(int directory, unsigned int id, unsigned int slot,
			   struct fttr_tcont_grant *grant);
int fttr_miniolt_tx_disable(int directory);
int fttr_miniolt_p6_stop(int ni);
int fttr_miniolt_command(int directory, const char *name, const char *value);
int fttr_miniolt_wait_links(int directory, int ni);
int fttr_miniolt_calibrated(int optical);

#endif
