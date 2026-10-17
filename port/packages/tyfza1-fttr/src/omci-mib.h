/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_MIB_H
#define TYFZA1_FTTR_OMCI_MIB_H

#include "omci-wire.h"

struct fttr_omci_session {
	int fd;
	struct fttr_omci_link link;
	uint16_t onu, transaction;
};

struct fttr_mib_record {
	uint16_t class_id, instance, mask;
	uint8_t attributes[26];
};

void fttr_mib_request(uint16_t transaction, bool next, uint16_t sequence, uint8_t body[44]);
void fttr_mib_record_decode(const uint8_t body[44], struct fttr_mib_record *record);
int fttr_mib_scan(struct fttr_omci_session *session, uint16_t *commands,
		 int (*record)(void *context, uint16_t sequence, const struct fttr_mib_record *entry),
		 void *context);

#endif
