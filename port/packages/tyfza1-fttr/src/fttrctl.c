// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/random.h>
#include <unistd.h>

#include "miniolt.h"
#include "omci-mib.h"
#include "omci-me.h"
#include "omci-gem.h"
#include "omci-socket.h"

static void hex(const uint8_t *data, size_t length)
{
	size_t i;

	for (i = 0; i < length; i++)
		printf("%02x", data[i]);
}

static int mib_record(void *context, uint16_t sequence, const struct fttr_mib_record *entry)
{
	(void)context;
	printf("{\"event\":\"mib_record\",\"sequence\":%u,\"class\":%u,\"instance\":%u,"
	       "\"mask\":%u,\"attributes\":\"", sequence, entry->class_id, entry->instance, entry->mask);
	hex(entry->attributes, sizeof(entry->attributes));
	puts("\"}");
	return ferror(stdout) ? -EIO : 0;
}

static int status(int directory)
{
	struct fttr_onu_state table[FTTR_ONUS];
	unsigned int id;
	int ret = fttr_miniolt_table(directory, table);

	if (ret)
		return ret;
	for (id = 0; id < FTTR_ONUS; id++) {
		struct fttr_onu_state *onu = &table[id];

		printf("{\"id\":%u,\"hardware_registered\":%s,\"blocked\":%s,"
		       "\"downstream\":%" PRIu32 ",\"upstream\":%" PRIu32 ",\"eqd\":%" PRIu32 ",\"serial\":\"",
		       id, onu->downstream & (1U << 18) ? "true" : "false",
		       onu->downstream & (1U << 16) ? "true" : "false",
		       onu->downstream, onu->upstream, onu->eqd);
		hex(onu->serial, sizeof(onu->serial));
		puts("\"}");
	}
	return ferror(stdout) ? -EIO : 0;
}

static int open_session(int directory, unsigned int id, const char *interface,
			struct fttr_omci_session *session, struct fttr_onu_state *before)
{
	uint16_t seed;
	int ret;

	if (flock(directory, LOCK_EX | LOCK_NB))
		return -errno;
	ret = fttr_miniolt_one(directory, id, before);
	if (ret)
		return ret;
	if (!(before->downstream & (1U << 18)))
		return -ENOLINK;
	if (before->downstream & (1U << 16))
		return -EACCES;
	ret = fttr_miniolt_link(directory, &session->link);
	if (ret)
		return ret;
	ret = getrandom(&seed, sizeof(seed), 0);
	if (ret < 0)
		return -errno;
	session->onu = id;
	session->transaction = seed | 0x8000;
	session->fd = fttr_omci_open(interface, session->link.ethertype);
	if (session->fd < 0)
		return session->fd;
	ret = fttr_miniolt_management(directory, id, true);
	if (ret) {
		close(session->fd);
		return ret;
	}
	return 0;
}

static int scan(int directory, unsigned int id, const char *interface)
{
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	uint16_t commands;
	int ret = open_session(directory, id, interface, &session, &before);

	if (ret)
		return ret;
	printf("{\"event\":\"mib_start\",\"id\":%u,\"serial\":\"", id);
	hex(before.serial, sizeof(before.serial));
	puts("\"}");
	ret = fttr_mib_scan(&session, &commands, mib_record, NULL);
	close(session.fd);
	if (ret)
		return ret;
	ret = fttr_miniolt_same(directory, id, &before);
	if (ret)
		return ret;
	printf("{\"event\":\"mib_complete\",\"id\":%u,\"commands\":%u}\n", id, commands);
	return ferror(stdout) ? -EIO : 0;
}

static int get(int directory, unsigned int id, const uint16_t fields[3], const char *interface)
{
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_me_job job;
	int result, ret = open_session(directory, id, interface, &session, &before);

	if (ret)
		return ret;
	ret = fttr_me_job_start(&job, id, &session.transaction, FTTR_ME_GET,
				fields[0], fields[1], fields[2], NULL, 0);
	if (!ret)
		ret = fttr_me_exchange(&session, &job);
	close(session.fd);
	if (job.replied) {
		result = fttr_miniolt_same(directory, id, &before);
		if (result)
			return result;
		printf("{\"event\":\"me_reply\",\"id\":%u,\"class\":%u,\"instance\":%u,"
		       "\"mask\":%u,\"result\":%u,\"error\":%d,\"body\":\"",
		       id, fields[0], fields[1], fields[2], job.result, ret);
		hex(job.reply, sizeof(job.reply));
		puts("\"}");
	}
	return ferror(stdout) ? -EIO : ret;
}

static int tcont_exchange(struct fttr_omci_session *session, struct fttr_me_job *job,
			  uint16_t instance, const uint8_t *value)
{
	int ret = fttr_me_job_start(job, session->onu, &session->transaction,
				   value ? FTTR_ME_SET : FTTR_ME_GET, 262, instance, 0x8000,
				   value, value ? 2 : 0);

	return ret ? ret : fttr_me_exchange(session, job);
}

static int bind_tcont(int directory, unsigned int id, const uint16_t fields[5], const char *interface)
{
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_me_job job;
	uint16_t alloc = fttr_miniolt_alloc_id(id, fields[0]), previous = 0, observed = 0;
	uint8_t value[2] = { alloc >> 8, alloc };
	bool read = false, sent = false, verified = false;
	const char *phase = "read";
	int ret = open_session(directory, id, interface, &session, &before);

	if (ret)
		return ret;
	ret = tcont_exchange(&session, &job, fields[1], NULL);
	if (ret)
		goto out;
	previous = observed = (uint16_t)job.reply[11] << 8 | job.reply[12];
	read = true;
	phase = "identity";
	ret = fttr_miniolt_same(directory, id, &before);
	if (ret)
		goto out;
	if (previous != alloc) {
		phase = "set";
		ret = tcont_exchange(&session, &job, fields[1], value);
		sent = job.attempts != 0;
		if (ret)
			goto out;
		phase = "verify";
		ret = tcont_exchange(&session, &job, fields[1], NULL);
		if (ret)
			goto out;
		observed = (uint16_t)job.reply[11] << 8 | job.reply[12];
		if (observed != alloc) {
			ret = -EUCLEAN;
			goto out;
		}
		phase = "identity";
		ret = fttr_miniolt_same(directory, id, &before);
		if (ret)
			goto out;
	}
	verified = true;
	phase = "grant";
	ret = fttr_miniolt_tcont(directory, id, fields[0], fields[2], fields[3], fields[4]);
	if (!ret)
		phase = "bound";
out:
	close(session.fd);
	printf("{\"event\":\"tcont_binding\",\"id\":%u,\"slot\":%u,\"instance\":%u,"
	       "\"alloc_id\":%u,\"phase\":\"%s\",\"error\":%d,\"remote_write_sent\":%s,"
	       "\"alloc_verified\":%s,\"previous_alloc_id\":",
	       id, fields[0], fields[1], alloc, phase, ret, sent ? "true" : "false", verified ? "true" : "false");
	if (read)
		printf("%u,\"last_read_alloc_id\":%u", previous, observed);
	else
		fputs("null,\"last_read_alloc_id\":null", stdout);
	if (job.replied) {
		printf(",\"result\":%u,\"body\":\"", job.result);
		hex(job.reply, sizeof(job.reply));
		fputc('"', stdout);
	}
	puts("}");
	return ferror(stdout) ? -EIO : ret;
}

static int create_gem(int directory, unsigned int id, const uint16_t fields[6], const char *interface)
{
	struct fttr_gem_config config = {
		.entity = fttr_miniolt_gem_id(id, fields[0]), .tcont = fields[1], .queue = fields[2],
		.bridge = fields[3], .profile = fields[4], .gal = fields[5],
	};
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_gem_job job;
	int result, ret;

	if (config.entity > 4090)
		return -ERANGE;
	ret = open_session(directory, id, interface, &session, &before);
	if (ret)
		return ret;
	fttr_gem_job_start(&job, id, &session.transaction, &config, before.serial);
	ret = fttr_gem_execute(&session, &job, directory, &before);
	close(session.fd);
	result = fttr_gem_job_dump(&job, stdout);
	return ret ? ret : result;
}

int main(int argc, char **argv)
{
	bool mib, wait, query, binding, gem;
	int directory, ret;

	if (argc < 3 || (strcmp(argv[2], "status") && strcmp(argv[2], "mib") &&
	    strcmp(argv[2], "wait-link") && strcmp(argv[2], "get") && strcmp(argv[2], "tcont") &&
	    strcmp(argv[2], "gem"))) {
		fprintf(stderr, "Usage: %s <miniolt-sysfs-directory> status\n"
			"       %s <miniolt-sysfs-directory> mib <hardware-ID> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> get <hardware-ID> <class> <instance> <mask> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> tcont <hardware-ID> <slot:1..7> <instance> <fixed> <assured> <maximum> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> gem <hardware-ID> <ordinal:1..60> <tcont-instance> <queue-instance> <association:0|1> <profile-instance> <gal-instance> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> wait-link <NI-sysfs-directory>\n",
			argv[0], argv[0], argv[0], argv[0], argv[0], argv[0]);
		return 2;
	}
	mib = !strcmp(argv[2], "mib");
	wait = !strcmp(argv[2], "wait-link");
	query = !strcmp(argv[2], "get");
	binding = !strcmp(argv[2], "tcont");
	gem = !strcmp(argv[2], "gem");
	if ((mib && argc != 4 && argc != 5) || (query && argc != 7 && argc != 8) ||
	    (binding && argc != 9 && argc != 10) || (gem && argc != 10 && argc != 11) ||
	    (wait && argc != 4) || (!mib && !wait && !query && !binding && !gem && argc != 3))
		return 2;
	directory = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (directory < 0) {
		perror(argv[1]);
		return 1;
	}
	if (mib || query || binding || gem) {
		char *end;
		unsigned long id;

		errno = 0;
		id = strtoul(argv[3], &end, 10);
		if (errno || !*argv[3] || *end || id >= FTTR_ONUS) {
			close(directory);
			return 2;
		}
		if (query || binding || gem) {
			uint16_t fields[6];
			unsigned int i;

			for (i = 0; i < (gem ? 6U : binding ? 5U : 3U); i++) {
				unsigned long value;

				errno = 0;
				value = strtoul(argv[4 + i], &end, 0);
				if (errno || !*argv[4 + i] || *end || value > UINT16_MAX ||
				    (query && i == 2 && !value) || (binding && i == 0 && (!value || value > 7)) ||
				    (gem && ((i == 0 && (!value || value > 60)) || (i == 3 && value > 1)))) {
					close(directory);
					return 2;
				}
				fields[i] = value;
			}
			if (gem)
				ret = create_gem(directory, id, fields, argc == 11 ? argv[10] : "fttr0");
			else if (binding)
				ret = bind_tcont(directory, id, fields, argc == 10 ? argv[9] : "fttr0");
			else
				ret = get(directory, id, fields, argc == 8 ? argv[7] : "fttr0");
		} else {
			ret = scan(directory, id, argc == 5 ? argv[4] : "fttr0");
		}
	} else if (wait) {
		int ni = open(argv[3], O_RDONLY | O_DIRECTORY | O_CLOEXEC);

		if (ni < 0) {
			ret = -errno;
		} else {
			ret = fttr_miniolt_wait_links(directory, ni);
			close(ni);
		}
	} else {
		ret = status(directory);
	}
	close(directory);
	if (!ret && fflush(stdout))
		ret = -errno;
	if (ret) {
		fprintf(stderr, "FTTR %s: %s (%d)\n", argv[2], strerror(-ret), ret);
		return 1;
	}
	return 0;
}
