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

#endif
