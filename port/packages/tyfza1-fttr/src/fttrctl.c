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

static int scan(int directory, unsigned int id, const char *interface)
{
	struct fttr_onu_state table[FTTR_ONUS], before;
	struct fttr_omci_session session = { .onu = id };
	uint16_t commands, seed;
	int ret;

	if (flock(directory, LOCK_EX | LOCK_NB))
		return -errno;
	ret = fttr_miniolt_table(directory, table);
	if (ret)
		return ret;
	before = table[id];
	if (!(before.downstream & (1U << 18)))
		return -ENOLINK;
	if (before.downstream & (1U << 16))
		return -EACCES;
	ret = fttr_miniolt_link(directory, &session.link);
	if (ret)
		return ret;
	ret = getrandom(&seed, sizeof(seed), 0);
	if (ret < 0)
		return -errno;
	session.transaction = seed | 0x8000;
	session.fd = fttr_omci_open(interface, session.link.ethertype);
	if (session.fd < 0)
		return session.fd;
	ret = fttr_miniolt_management(directory, id, true);
	if (ret) {
		close(session.fd);
		return ret;
	}
	printf("{\"event\":\"mib_start\",\"id\":%u,\"serial\":\"", id);
	hex(before.serial, sizeof(before.serial));
	puts("\"}");
	ret = fttr_mib_scan(&session, &commands, mib_record, NULL);
	close(session.fd);
	if (ret)
		return ret;
	ret = fttr_miniolt_one(directory, id, &table[id]);
	if (ret)
		return ret;
	if (!(table[id].downstream & (1U << 18)) || (table[id].downstream & (1U << 16)) ||
	    memcmp(before.serial, table[id].serial, sizeof(before.serial)))
		return -ESTALE;
	printf("{\"event\":\"mib_complete\",\"id\":%u,\"commands\":%u}\n", id, commands);
	return ferror(stdout) ? -EIO : 0;
}

int main(int argc, char **argv)
{
	bool mib, wait;
	int directory, ret;

	if (argc < 3 || (strcmp(argv[2], "status") && strcmp(argv[2], "mib") && strcmp(argv[2], "wait-link"))) {
		fprintf(stderr, "Usage: %s <miniolt-sysfs-directory> status\n"
			"       %s <miniolt-sysfs-directory> mib <hardware-ID> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> wait-link <NI-sysfs-directory>\n",
			argv[0], argv[0], argv[0]);
		return 2;
	}
	mib = !strcmp(argv[2], "mib");
	wait = !strcmp(argv[2], "wait-link");
	if ((mib && argc != 4 && argc != 5) || (wait && argc != 4) || (!mib && !wait && argc != 3))
		return 2;
	directory = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (directory < 0) {
		perror(argv[1]);
		return 1;
	}
	if (mib) {
		char *end;
		unsigned long id;

		errno = 0;
		id = strtoul(argv[3], &end, 10);
		if (errno || !*argv[3] || *end || id >= FTTR_ONUS) {
			close(directory);
			return 2;
		}
		ret = scan(directory, id, argc == 5 ? argv[4] : "fttr0");
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
