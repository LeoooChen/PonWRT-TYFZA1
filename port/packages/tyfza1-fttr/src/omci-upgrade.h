/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_UPGRADE_H
#define TYFZA1_FTTR_OMCI_UPGRADE_H

#include "omci-me.h"

enum fttr_upgrade_action { FTTR_UPGRADE_DOWNLOAD, FTTR_UPGRADE_ACTIVATE, FTTR_UPGRADE_COMMIT };
enum fttr_upgrade_stage { FTTR_UPGRADE_SELECT0, FTTR_UPGRADE_SELECT1,
	FTTR_UPGRADE_START, FTTR_UPGRADE_SECTION, FTTR_UPGRADE_END, FTTR_UPGRADE_END_DELAY,
	FTTR_UPGRADE_VERIFY, FTTR_UPGRADE_ACTIVATING, FTTR_UPGRADE_COMMITTING,
	FTTR_UPGRADE_DONE, FTTR_UPGRADE_FAILED };

struct fttr_upgrade_job {
	struct fttr_me_job me;
	enum fttr_upgrade_action action;
	enum fttr_upgrade_stage stage;
	/* Caller retains an immutable image until this job terminates. */
	const uint8_t *data;
	uint32_t size, crc, confirmed, window_bytes;
	uint16_t image, window, section, sections;
	uint8_t attributes[2][17]; /* version14, committed, active, valid */
	unsigned int retries, end_retries;
	bool waiting, accepted, verified;
	int error;
};

int fttr_upgrade_start(struct fttr_upgrade_job *job, uint16_t onu, uint16_t *counter,
		       enum fttr_upgrade_action action, const uint8_t *data, size_t size);
/* One frame per call lets the resident dispatcher service other ONUs. Caller
 * checks registration identity before every send and before accepting replies.
 */
int fttr_upgrade_send(struct fttr_upgrade_job *job, uint16_t *counter, int fd,
		      const struct fttr_omci_link *link, int64_t now);
int fttr_upgrade_reply(struct fttr_upgrade_job *job, uint16_t *counter,
		       const struct fttr_omci_message *message, int64_t now);
int fttr_upgrade_tick(struct fttr_upgrade_job *job, uint16_t *counter, int64_t now);
void fttr_upgrade_cancel(struct fttr_upgrade_job *job, int error);
#endif
