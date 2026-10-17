// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <unistd.h>

#include "miniolt.h"

static FILE *attribute(int directory, const char *name)
{
	int fd = openat(directory, name, O_RDONLY | O_CLOEXEC);
	FILE *file;

	if (fd < 0)
		return NULL;
	file = fdopen(fd, "r");
	if (!file) {
		int error = errno;

		close(fd);
		errno = error;
	}
	return file;
}

int fttr_miniolt_table(int directory, struct fttr_onu_state table[FTTR_ONUS])
{
	FILE *file = attribute(directory, "onu_table");
	unsigned int row, id;
	int ret = 0, fields;

	if (!file)
		return -errno;
	for (row = 0; row < FTTR_ONUS; row++) {
		struct fttr_onu_state *onu = &table[row];

		fields = fscanf(file, "%u %" SCNx32 " %" SCNx32 " %" SCNx32
			" %2hhx%2hhx%2hhx%2hhx%2hhx%2hhx%2hhx%2hhx",
			&id, &onu->downstream, &onu->upstream, &onu->eqd,
			&onu->serial[0], &onu->serial[1], &onu->serial[2], &onu->serial[3],
			&onu->serial[4], &onu->serial[5], &onu->serial[6], &onu->serial[7]);
		if (fields != 12 || id != row) {
			ret = ferror(file) ? -errno : -EPROTO;
			break;
		}
	}
	fclose(file);
	return ret;
}

int fttr_miniolt_link(int directory, struct fttr_omci_link *link)
{
	FILE *file = attribute(directory, "omci_link");
	unsigned int downstream_length;
	int ret, fields;

	if (!file)
		return -errno;
	fields = fscanf(file, "host=%2hhx:%2hhx:%2hhx:%2hhx:%2hhx:%2hhx "
		"olt=%2hhx:%2hhx:%2hhx:%2hhx:%2hhx:%2hhx ethertype=%" SCNx16 " downstream_length=%u",
		&link->host_mac[0], &link->host_mac[1], &link->host_mac[2],
		&link->host_mac[3], &link->host_mac[4], &link->host_mac[5],
		&link->olt_mac[0], &link->olt_mac[1], &link->olt_mac[2],
		&link->olt_mac[3], &link->olt_mac[4], &link->olt_mac[5],
		&link->ethertype, &downstream_length);
	ret = fields == 14 ? 0 : ferror(file) ? -errno : -EPROTO;
	if (!ret)
		link->downstream_length = downstream_length;
	fclose(file);
	return ret;
}

int fttr_miniolt_links_ready(int directory, int ni)
{
	FILE *file = attribute(directory, "host_serdes");
	unsigned int peer_clock, peer_link, host_clock, host_link;
	int error, fields, ret;

	if (!file)
		return -errno;
	fields = fscanf(file, "mode=%*u nway=%*u pll=%u error=%d link=%u", &peer_clock, &error, &peer_link);
	ret = fields == 3 ? error : ferror(file) ? -errno : -EPROTO;
	fclose(file);
	if (ret)
		return ret;
	file = attribute(ni, "fttr_link");
	if (!file)
		return -errno;
	fields = fscanf(file, "configured=%*u mode=%*u nway=%*u clock=%u error=%d link=%u",
			&host_clock, &error, &host_link);
	ret = fields == 3 ? error : ferror(file) ? -errno : -EPROTO;
	fclose(file);
	return ret ? ret : !!(peer_clock && peer_link && host_clock && host_link);
}
