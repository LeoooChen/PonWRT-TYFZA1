/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_ME_H
#define TYFZA1_FTTR_OMCI_ME_H

#include "omci-mib.h"
#include "miniolt.h"

enum fttr_me_operation { FTTR_ME_CREATE = 0x44, FTTR_ME_DELETE = 0x46,
	FTTR_ME_SET = 0x48, FTTR_ME_GET = 0x49, FTTR_ME_GET_NEXT = 0x5a };
enum fttr_me_state { FTTR_ME_WAIT, FTTR_ME_DONE, FTTR_ME_FAILED };

struct fttr_me_job {
	enum fttr_me_state state;
	uint16_t onu, transaction, mask;
	unsigned int attempts;
	int64_t deadline;
	int error;
	bool replied;
	uint8_t result, request[44], reply[44];
};

/* Values are in wire order. GET_NEXT takes a two-byte sequence number. */
int fttr_me_job_start(struct fttr_me_job *job, uint16_t onu, uint16_t *counter,
		      enum fttr_me_operation operation, uint16_t class_id, uint16_t instance,
		      uint16_t mask, const uint8_t *values, size_t size);
int fttr_me_job_send(struct fttr_me_job *job, int fd, const struct fttr_omci_link *link, int64_t now);
int fttr_me_job_timeout(struct fttr_me_job *job, int fd, const struct fttr_omci_link *link, int64_t now);
/* Zero ignores an unrelated reply, one completes, a negative result fails. */
int fttr_me_job_reply(struct fttr_me_job *job, const struct fttr_omci_message *message);
int fttr_me_exchange(struct fttr_omci_session *session, struct fttr_me_job *job);
int fttr_me_exchange_same(struct fttr_omci_session *session, struct fttr_me_job *job,
			 int directory, const struct fttr_onu_state *identity);

#endif
