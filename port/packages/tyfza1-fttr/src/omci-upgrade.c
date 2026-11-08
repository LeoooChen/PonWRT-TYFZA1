// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <string.h>
#include "omci-upgrade.h"
#include "omci-wire.h"

/* Target relay requests31 sections and arms300 ticks of its100ms timer.
 * End-download rejection may mean flash is still busy:120 retries at4s.
 */
#define UPGRADE_WINDOW 31
#define UPGRADE_TIMEOUT 30000

static void be32(uint8_t *p, uint32_t v)
{
	p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}

void fttr_upgrade_cancel(struct fttr_upgrade_job *job, int error)
{
	job->error = error;
	job->stage = FTTR_UPGRADE_FAILED;
	job->waiting = false;
	job->me.state = FTTR_ME_FAILED;
	job->me.error = error;
}

static int fail(struct fttr_upgrade_job *job, int error)
{
	fttr_upgrade_cancel(job, error);
	return error;
}

static void prepare(struct fttr_upgrade_job *job, uint16_t *counter)
{
	uint16_t onu = job->me.onu;
	unsigned int operation = FTTR_ME_GET, image = job->image;

	job->waiting = false;
	switch (job->stage) {
	case FTTR_UPGRADE_SELECT0: image = 0; break;
	case FTTR_UPGRADE_SELECT1: image = 1; break;
	case FTTR_UPGRADE_START: operation = 0x53; break;
	case FTTR_UPGRADE_SECTION: operation = job->section + 1 == job->sections ? 0x54 : 0x14; break;
	case FTTR_UPGRADE_END: operation = 0x55; break;
	case FTTR_UPGRADE_ACTIVATING: operation = 0x56; break;
	case FTTR_UPGRADE_COMMITTING: operation = 0x57; break;
	default: break;
	}
	fttr_me_job_start(&job->me, onu, counter, operation, 7, image,
		operation == FTTR_ME_GET ? 0xf000 : 0, NULL, 0);
	/* Retransmission is controlled by the image/window state machine. */
	job->me.no_retry = true;
	if (job->stage == FTTR_UPGRADE_START) {
		job->me.request[8] = UPGRADE_WINDOW - 1;
		be32(job->me.request + 9, job->size);
	} else if (job->stage == FTTR_UPGRADE_SECTION) {
		uint32_t offset = job->confirmed + job->section * 31;
		size_t size = job->size - offset;
		if (size > 31) size = 31;
		job->me.request[8] = job->section;
		memcpy(job->me.request + 9, job->data + offset, size);
	} else if (job->stage == FTTR_UPGRADE_END) {
		be32(job->me.request + 8, job->crc);
		be32(job->me.request + 12, job->size);
	}
}

static void window_start(struct fttr_upgrade_job *job, uint16_t *counter)
{
	job->window_bytes = job->size - job->confirmed;
	if (job->window_bytes > job->window * 31U) job->window_bytes = job->window * 31U;
	job->sections = (job->window_bytes + 30) / 31;
	job->section = 0;
	job->stage = FTTR_UPGRADE_SECTION;
	prepare(job, counter);
}

int fttr_upgrade_start(struct fttr_upgrade_job *job, uint16_t onu, uint16_t *counter,
		       enum fttr_upgrade_action action, const uint8_t *data, size_t size)
{
	memset(job, 0, sizeof(*job));
	job->me.onu = onu;
	job->action = action;
	if (onu >= 64 || action > FTTR_UPGRADE_COMMIT || action < FTTR_UPGRADE_DOWNLOAD ||
	    (action == FTTR_UPGRADE_DOWNLOAD ? !data || !size || size > UINT32_MAX : data || size))
		return fail(job, -EINVAL);
	job->data = data;
	job->size = size;
	job->crc = fttr_omci_crc(data, size);
	job->stage = FTTR_UPGRADE_SELECT0;
	prepare(job, counter);
	return 0;
}

int fttr_upgrade_send(struct fttr_upgrade_job *job, uint16_t *counter, int fd,
		      const struct fttr_omci_link *link, int64_t now)
{
	int ret;

	if (job->stage == FTTR_UPGRADE_FAILED) return job->error;
	if (job->waiting || job->stage == FTTR_UPGRADE_DONE || job->stage == FTTR_UPGRADE_END_DELAY)
		return 0;
	ret = fttr_me_job_send(&job->me, fd, link, now);
	if (ret) return fail(job, ret);
	if (job->stage == FTTR_UPGRADE_SECTION && job->section + 1 < job->sections) {
		job->section++;
		prepare(job, counter);
	} else {
		job->waiting = true;
		job->me.deadline = now + UPGRADE_TIMEOUT;
	}
	return 0;
}

static int selected(struct fttr_upgrade_job *job, uint16_t *counter)
{
	unsigned int i;

	for (i = 0; i < 2; i++) {
		const uint8_t *a = job->attributes[i];
		if (a[14] > 1 || a[15] > 1 || a[16] > 1) return fail(job, -EPROTO);
	}
	for (i = 0; i < 2; i++) {
		const uint8_t *a = job->attributes[i];
		bool eligible = job->action == FTTR_UPGRADE_DOWNLOAD ? !a[14] && !a[15] :
			job->action == FTTR_UPGRADE_ACTIVATE ? a[16] && !a[15] : a[16] && !a[14];
		if (eligible) break;
	}
	if (i == 2) return fail(job, -ENOENT);
	job->image = i;
	job->stage = job->action == FTTR_UPGRADE_DOWNLOAD ? FTTR_UPGRADE_START :
		job->action == FTTR_UPGRADE_ACTIVATE ? FTTR_UPGRADE_ACTIVATING : FTTR_UPGRADE_COMMITTING;
	prepare(job, counter);
	return 1;
}

int fttr_upgrade_reply(struct fttr_upgrade_job *job, uint16_t *counter,
		       const struct fttr_omci_message *message, int64_t now)
{
	int ret;

	if (!job->waiting || message->length < 2) return 0;
	ret = fttr_me_job_reply(&job->me, message);
	if (!ret) return 0;
	job->waiting = false;
	if (ret < 0) {
		if (ret == -EREMOTEIO && job->stage == FTTR_UPGRADE_SECTION && job->retries < 2) {
			job->retries++;
			window_start(job, counter);
			return 1;
		}
		if (ret == -EREMOTEIO && job->stage == FTTR_UPGRADE_END && job->end_retries < 120) {
			job->end_retries++;
			job->stage = FTTR_UPGRADE_END_DELAY;
			job->me.deadline = now + 4000;
			return 1;
		}
		return fail(job, ret);
	}
	switch (job->stage) {
	case FTTR_UPGRADE_SELECT0:
	case FTTR_UPGRADE_SELECT1:
		memcpy(job->attributes[job->stage == FTTR_UPGRADE_SELECT1], job->me.reply + 11, 17);
		if (job->stage == FTTR_UPGRADE_SELECT1) return selected(job, counter);
		job->stage = FTTR_UPGRADE_SELECT1;
		break;
	case FTTR_UPGRADE_START:
		job->window = (unsigned int)job->me.reply[9] + 1;
		if (job->window > UPGRADE_WINDOW) return fail(job, -EPROTO);
		window_start(job, counter);
		return 1;
	case FTTR_UPGRADE_SECTION:
		if (job->me.reply[9] != job->section) return fail(job, -EPROTO);
		job->confirmed += job->window_bytes;
		job->retries = 0;
		if (job->confirmed < job->size) {
			window_start(job, counter);
			return 1;
		}
		job->stage = FTTR_UPGRADE_END;
		break;
	case FTTR_UPGRADE_END:
	case FTTR_UPGRADE_COMMITTING:
		job->accepted = true;
		job->stage = FTTR_UPGRADE_VERIFY;
		break;
	case FTTR_UPGRADE_ACTIVATING:
		/* Activation can reboot the peer: caller must observe its return
		 * and perform fresh identity/image checks, never resend here.
		 */
		job->accepted = true;
		job->stage = FTTR_UPGRADE_DONE;
		return 1;
	case FTTR_UPGRADE_VERIFY:
		if (job->me.reply[27] != 1 || (job->action == FTTR_UPGRADE_COMMIT ?
		    job->me.reply[25] != 1 : job->me.reply[25] || job->me.reply[26]))
			return fail(job, -EUCLEAN);
		job->verified = true;
		job->stage = FTTR_UPGRADE_DONE;
		return 1;
	default: return fail(job, -EPROTO);
	}
	prepare(job, counter);
	return 1;
}

int fttr_upgrade_tick(struct fttr_upgrade_job *job, uint16_t *counter, int64_t now)
{
	if (job->stage == FTTR_UPGRADE_FAILED) return job->error;
	if (job->stage == FTTR_UPGRADE_END_DELAY && now >= job->me.deadline) {
		job->stage = FTTR_UPGRADE_END;
		prepare(job, counter);
		return 1;
	}
	/* Factory transaction expiry does not establish remote rejection. */
	if (job->waiting && now >= job->me.deadline) return fail(job, -ETIMEDOUT);
	return 0;
}
