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

enum fttr_mib_step { FTTR_MIB_IDLE, FTTR_MIB_UPLOAD, FTTR_MIB_NEXT, FTTR_MIB_DONE, FTTR_MIB_FAILED };
enum fttr_mib_action { FTTR_MIB_RECORD = 1, FTTR_MIB_REQUEST = 2, FTTR_MIB_COMPLETE = 4 };

struct fttr_mib_job {
	enum fttr_mib_step step;
	uint16_t onu, transaction, sequence, commands;
	unsigned int attempts;
	int64_t deadline;
	int error;
	uint8_t request[44];
};

void fttr_mib_job_start(struct fttr_mib_job *job, uint16_t onu, uint16_t *counter);
int fttr_mib_job_send(struct fttr_mib_job *job, int fd, const struct fttr_omci_link *link, int64_t now);
int fttr_mib_job_timeout(struct fttr_mib_job *job, int fd, const struct fttr_omci_link *link, int64_t now);
int fttr_mib_job_reply(struct fttr_mib_job *job, const struct fttr_omci_message *message,
		       uint16_t *counter, struct fttr_mib_record *record);

void fttr_mib_request(uint16_t transaction, bool next, uint16_t sequence, uint8_t body[44]);
void fttr_mib_record_decode(const uint8_t body[44], struct fttr_mib_record *record);
int fttr_mib_scan(struct fttr_omci_session *session, uint16_t *commands,
		 int (*record)(void *context, uint16_t sequence, const struct fttr_mib_record *entry),
		 void *context);

#endif
