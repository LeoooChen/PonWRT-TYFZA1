// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "omci-auth.h"

int fttr_auth_config_read(const char *path, struct fttr_auth_config *config)
{
	unsigned char data[43];
	struct stat st;
	ssize_t size;
	int fd, ret = 0;

	memset(config, 0, sizeof(*config));
	fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0)
		return -errno;
	if (fstat(fd, &st))
		ret = -errno;
	else if (!S_ISREG(st.st_mode) || (st.st_mode & 0077) || st.st_uid != geteuid())
		ret = -EACCES;
	else if (st.st_size != 42)
		ret = -EMSGSIZE;
	if (!ret) {
		size = read(fd, data, sizeof(data));
		if (size < 0)
			ret = -errno;
		else if (size != 42)
			ret = -EMSGSIZE;
		else if (memcmp(data, "TFL1", 4) || data[28] || data[41])
			ret = -EPROTO;
		else {
			memcpy(config->loid, data + 4, 25);
			memcpy(config->key, data + 29, 13);
			config->enabled = config->loid[0] && config->key[0];
		}
	}
	close(fd);
	return ret;
}

void fttr_auth_cancel(struct fttr_auth_job *job, int error)
{
	job->phase = FTTR_AUTH_FAILED;
	job->error = error;
	job->verified = false;
	memset(job->loid, 0, sizeof(job->loid));
	memset(job->key, 0, sizeof(job->key));
	memset(&job->me, 0, sizeof(job->me));
}

static int request(struct fttr_auth_job *job, uint16_t *counter)
{
	uint16_t onu = job->me.onu, cls = 0xfffa, mask;
	enum fttr_me_operation operation = FTTR_ME_GET;

	switch (job->phase) {
	case FTTR_AUTH_CTC_LOID: mask = 0x4000; break;
	case FTTR_AUTH_CTC_KEY: mask = 0x2000; break;
	case FTTR_AUTH_ONUG_LOID: cls = 256; mask = 0x0040; break;
	case FTTR_AUTH_ONUG_KEY: cls = 256; mask = 0x0020; break;
	case FTTR_AUTH_WRITE: mask = 0x1000; operation = FTTR_ME_SET; break;
	case FTTR_AUTH_VERIFY: mask = 0x1000; break;
	default: return -EINVAL;
	}
	return fttr_me_job_start(&job->me, onu, counter, operation, cls, 0, mask,
		operation == FTTR_ME_SET ? &job->result : NULL, operation == FTTR_ME_SET ? 1 : 0);
}

int fttr_auth_start(struct fttr_auth_job *job, const struct fttr_auth_config *config,
		uint16_t onu, uint16_t *counter)
{
	memset(job, 0, sizeof(*job));
	if (!config->enabled)
		return 0;
	job->phase = FTTR_AUTH_CTC_LOID;
	job->me.onu = onu;
	return request(job, counter);
}

int fttr_auth_credentials(struct fttr_auth_job *job, bool acquire, uint16_t onu, uint16_t *counter)
{
	memset(job, 0, sizeof(*job));
	job->gather_only = true;
	job->phase = acquire ? FTTR_AUTH_CTC_LOID : FTTR_AUTH_READY;
	job->me.onu = onu;
	return acquire ? request(job, counter) : 0;
}

int fttr_auth_decide(struct fttr_auth_job *job, const struct fttr_auth_config *config, uint16_t *counter)
{
	int ret;

	if (job->phase != FTTR_AUTH_READY)
		return -EINVAL;
	if (config->enabled && !job->acquired) {
		fttr_auth_cancel(job, -ENODATA);
		return -ENODATA;
	}
	job->result = !config->enabled ? 0 : strcmp(config->loid, job->loid) ? 2 : strcmp(config->key, job->key) ? 3 : 1;
	memset(job->loid, 0, sizeof(job->loid));
	memset(job->key, 0, sizeof(job->key));
	if (!config->enabled) {
		job->phase = FTTR_AUTH_DONE;
		memset(&job->me, 0, sizeof(job->me));
		return 0;
	}
	job->phase = FTTR_AUTH_WRITE;
	ret = request(job, counter);
	if (ret)
		fttr_auth_cancel(job, ret);
	return ret ? ret : 1;
}

int fttr_auth_advance(struct fttr_auth_job *job, const struct fttr_auth_config *config,
		uint16_t *counter)
{
	int ret;

	if (job->phase == FTTR_AUTH_DISABLED || job->phase >= FTTR_AUTH_DONE)
		return 0;
	if (job->me.state == FTTR_ME_WAIT)
		return -EINPROGRESS;
	if (job->me.state == FTTR_ME_FAILED) {
		ret = job->me.error;
		/* Original retries through ONU-G if either CTC attribute fails.
		 * A malformed frame or a local transport/identity error cannot
		 * establish a peer capability difference.
		 */
		if (job->phase <= FTTR_AUTH_CTC_KEY && (ret == -EREMOTEIO || ret == -ETIMEDOUT)) {
			job->fallback_error = ret;
			memset(job->loid, 0, sizeof(job->loid));
			job->phase = FTTR_AUTH_ONUG_LOID;
		} else {
			fttr_auth_cancel(job, ret);
			return 0;
		}
	} else {
		switch (job->phase) {
		case FTTR_AUTH_CTC_LOID:
		case FTTR_AUTH_ONUG_LOID:
			memcpy(job->loid, job->me.reply + 11, 24);
			job->loid[24] = 0;
			job->phase++;
			break;
		case FTTR_AUTH_CTC_KEY:
		case FTTR_AUTH_ONUG_KEY:
			memcpy(job->key, job->me.reply + 11, 12);
			job->key[12] = 0;
			job->acquired = true;
			job->phase = FTTR_AUTH_READY;
			/* Preserve the ONU for the result transaction, erase raw replies. */
			memset(job->me.reply, 0, sizeof(job->me.reply));
			return job->gather_only ? 0 : fttr_auth_decide(job, config, counter);
		case FTTR_AUTH_WRITE:
			job->phase = FTTR_AUTH_VERIFY;
			break;
		case FTTR_AUTH_VERIFY:
			if (job->me.reply[11] != job->result) {
				fttr_auth_cancel(job, -EUCLEAN);
				return 0;
			}
			job->verified = true;
			job->phase = FTTR_AUTH_DONE;
			job->error = job->result == 1 ? 0 : -EACCES;
			memset(&job->me, 0, sizeof(job->me));
			return 0;
		default: return -EINVAL;
		}
	}
	ret = request(job, counter);
	if (ret)
		fttr_auth_cancel(job, ret);
	return ret ? ret : 1;
}
