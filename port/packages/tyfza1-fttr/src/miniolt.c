// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
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

int fttr_miniolt_command(int directory, const char *name, const char *value)
{
	int fd = openat(directory, name, O_WRONLY | O_CLOEXEC);
	ssize_t size;
	int ret;

	if (fd < 0)
		return -errno;
	size = write(fd, value, strlen(value));
	ret = size < 0 ? -errno : (size_t)size == strlen(value) ? 0 : -EIO;
	close(fd);
	return ret;
}

int fttr_miniolt_admin(int directory, unsigned int id, unsigned int action, const uint8_t serial[8])
{
	char command[40];
	unsigned int i, length;

	if (id >= FTTR_ONUS || action > 3)
		return -EINVAL;
	length = snprintf(command, sizeof(command), "%u %u ", id, action);
	for (i = 0; i < 8; i++)
		length += snprintf(command + length, sizeof(command) - length, "%02x", serial[i]);
	snprintf(command + length, sizeof(command) - length, "\n");
	return fttr_miniolt_command(directory, "onu_admin", command);
}

int fttr_miniolt_move(int directory, unsigned int from, unsigned int to,
		const uint8_t serial[8], const uint8_t target[8])
{
	char command[64];
	unsigned int i, length;

	if (from >= FTTR_ONUS || to >= FTTR_ONUS || from == to)
		return -EINVAL;
	length = snprintf(command, sizeof(command), "%u %u ", from, to);
	for (i = 0; i < 8; i++)
		length += snprintf(command + length, sizeof(command) - length, "%02x", serial[i]);
	command[length++] = ' ';
	for (i = 0; i < 8; i++)
		length += snprintf(command + length, sizeof(command) - length, "%02x", target[i]);
	snprintf(command + length, sizeof(command) - length, "\n");
	return fttr_miniolt_command(directory, "onu_move", command);
}

int fttr_miniolt_management(int directory, unsigned int id, bool enable)
{
	char value[16];
	int backend = fttr_miniolt_backend(directory);

	if (backend < 0)
		return backend;
	/* libponrelay1p25g configures OMCI globally in plr_fpga_init; its
	 * per-ONU OMCI GEM entry point performs no IO. No service grant is owned
	 * by an OMCI session on FPGA, so opening/closing it must not overwrite DBA.
	 */
	if (backend != FTTR_ASIC9690)
		return 0;
	snprintf(value, sizeof(value), "%u %u\n", id, enable);
	return fttr_miniolt_command(directory, "management_grant", value);
}

int fttr_miniolt_backend(int directory)
{
	FILE *file = attribute(directory, "backend");
	char name[16];
	int ret;

	if (!file)
		return -errno;
	if (!fgets(name, sizeof(name), file))
		ret = ferror(file) ? -errno : -EPROTO;
	else if (!strcmp(name, "asic9690\n"))
		ret = FTTR_ASIC9690;
	else if (!strcmp(name, "fpga1\n"))
		ret = FTTR_FPGA1;
	else if (!strcmp(name, "fpga2\n"))
		ret = FTTR_FPGA2;
	else
		ret = -EPROTO;
	fclose(file);
	return ret;
}

int fttr_miniolt_alloc_id(enum fttr_backend backend, unsigned int id, unsigned int slot)
{
	if (id >= FTTR_ONUS || !slot || slot > 7)
		return -EINVAL;
	/* The FPGA relay's declared Alloc-ID pool has only one 64-ONU row. */
	if (backend == FTTR_FPGA1)
		return slot == 1 ? 256 + (int)id : -ERANGE;
	/* FPGA2's retained diagnostic DBA table uses ASIC slot addressing, but
	 * its relay allocates the FPGA1 IDs. Their physical binding is not yet
	 * established; never assign a remote ID while guessing its local grant.
	 */
	if (backend == FTTR_FPGA2)
		return -EOPNOTSUPP;
	if (backend != FTTR_ASIC9690)
		return -EINVAL;
	/* Original ASIC allocator: 257 + (ONU - 1) * 8 + slot - 1. */
	return 256 + id * 8 + slot;
}

uint16_t fttr_miniolt_gem_id(unsigned int id, unsigned int ordinal)
{
	return 256 + id + (ordinal - 1) * 64;
}

int fttr_miniolt_tcont(int directory, unsigned int id, unsigned int slot,
		       unsigned int fixed, unsigned int assured, unsigned int maximum)
{
	char value[48];
	int backend = fttr_miniolt_backend(directory);

	if (backend < 0)
		return backend;
	if (backend == FTTR_FPGA2)
		return -EOPNOTSUPP;
	if (backend == FTTR_FPGA1) {
		if (slot != 1)
			return -ERANGE;
		slot = 0;
	}
	snprintf(value, sizeof(value), "%u %u %u %u %u\n", id, slot, fixed, assured, maximum);
	return fttr_miniolt_command(directory, "tcont", value);
}

int fttr_miniolt_tx_disable(int directory)
{
	return fttr_miniolt_command(directory, "tx_enable", "0\n");
}

int fttr_miniolt_p6_stop(int ni)
{
	return fttr_miniolt_command(ni, "fttr_link", "0 0\n");
}

int fttr_miniolt_p6_prepare(int ni, unsigned int nway)
{
	FILE *file = attribute(ni, "fttr_link");
	unsigned int configured, mode, observed_nway, clock;
	char command[16];
	int error, fields;

	if (!file)
		return -errno;
	fields = fscanf(file, "configured=%u mode=%u nway=%u clock=%u error=%d",
		&configured, &mode, &observed_nway, &clock, &error);
	fclose(file);
	if (fields != 5)
		return -EPROTO;
	/* Cold FPGA startup already configured P6 before loading. Preserve its
	 * clock through post-DONE initialization; warm teardown may have stopped it.
	 */
	if (configured && mode == 5 && observed_nway == nway && clock && !error)
		return 0;
	snprintf(command, sizeof(command), "5 %u\n", nway);
	return fttr_miniolt_command(ni, "fttr_link", command);
}

static uint32_t read_le32(const uint8_t *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int fttr_miniolt_tcont_read(int directory, unsigned int id, unsigned int slot,
			   struct fttr_tcont_grant *grant)
{
	uint8_t data[8];
	int backend = fttr_miniolt_backend(directory), fd, ret;
	ssize_t size;

	if (backend < 0)
		return backend;
	if (backend == FTTR_FPGA2)
		return -EOPNOTSUPP;
	if (backend == FTTR_FPGA1) {
		if (slot != 1)
			return -ERANGE;
		slot = 0;
	}
	fd = openat(directory, "tcont_state", O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -errno;
	size = pread(fd, data, sizeof(data), (id * 8 + slot) * sizeof(data));
	ret = size < 0 ? -errno : size == sizeof(data) ? 0 : -EPROTO;
	close(fd);
	if (ret)
		return ret;
	grant->fixed = read_le32(data);
	grant->limits = read_le32(data + 4);
	return 0;
}

int fttr_miniolt_one(int directory, unsigned int id, struct fttr_onu_state *onu)
{
	uint8_t data[20];
	int fd = openat(directory, "onu_state", O_RDONLY | O_CLOEXEC), ret;
	ssize_t size;

	if (fd < 0)
		return -errno;
	size = pread(fd, data, sizeof(data), id * sizeof(data));
	ret = size < 0 ? -errno : size == sizeof(data) ? 0 : -EPROTO;
	close(fd);
	if (ret)
		return ret;
	onu->downstream = read_le32(data);
	onu->upstream = read_le32(data + 4);
	onu->eqd = read_le32(data + 8);
	memcpy(onu->serial, data + 12, 8);
	return 0;
}

int fttr_miniolt_same(int directory, unsigned int id, const struct fttr_onu_state *expected)
{
	struct fttr_onu_state current;
	int ret = fttr_miniolt_one(directory, id, &current);

	if (ret)
		return ret;
	if (!(current.downstream & (1U << 18)) || (current.downstream & (1U << 16)) ||
	    memcmp(expected->serial, current.serial, sizeof(expected->serial)))
		return -ESTALE;
	return 0;
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
	FILE *file;
	unsigned int peer_clock, peer_link, host_clock, host_link;
	int error, fields, ret = fttr_miniolt_backend(directory);

	if (ret < 0)
		return ret;
	/* FPGA readiness is established by its manager dependency and the SoC
	 * P6 PCS. The WTG peer SerDes window exists only on ASIC9690.
	 */
	if (ret == FTTR_ASIC9690) {
		file = attribute(directory, "host_serdes");
		if (!file)
			return -errno;
		fields = fscanf(file, "mode=%*u nway=%*u pll=%u error=%d link=%u", &peer_clock, &error, &peer_link);
		ret = fields == 3 ? error : ferror(file) ? -errno : -EPROTO;
		fclose(file);
		if (ret)
			return ret;
		if (!peer_clock || !peer_link)
			return 0;
	}
	file = attribute(ni, "fttr_link");
	if (!file)
		return -errno;
	fields = fscanf(file, "configured=%*u mode=%*u nway=%*u clock=%u error=%d link=%u",
			&host_clock, &error, &host_link);
	ret = fields == 3 ? error : ferror(file) ? -errno : -EPROTO;
	fclose(file);
	return ret ? ret : !!(host_clock && host_link);
}

static int64_t monotonic_ms(void)
{
	struct timespec now;

	clock_gettime(CLOCK_MONOTONIC, &now);
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

int fttr_miniolt_wait_links(int directory, int ni)
{
	const struct timespec settle = { .tv_nsec = 500000000 };
	const struct timespec interval = { .tv_nsec = 20000000 };
	int64_t deadline = monotonic_ms() + 700;
	int ret;

	if (nanosleep(&settle, NULL))
		return -errno;
	for (;;) {
		ret = fttr_miniolt_links_ready(directory, ni);
		if (ret < 0)
			return ret;
		if (ret)
			return 0;
		if (monotonic_ms() >= deadline)
			return -ETIMEDOUT;
		if (nanosleep(&interval, NULL))
			return -errno;
	}
}

int fttr_miniolt_calibrated(int optical)
{
	FILE *file = attribute(optical, "calibration");
	unsigned int configured, marker, writes;
	char model[16];
	int error, fields, ret;

	if (!file)
		return -errno;
	fields = fscanf(file, "configured=%u model=%15s marker=%x writes=%u error=%d",
		&configured, model, &marker, &writes, &error);
	ret = fields == 5 ? error : ferror(file) ? -errno : -EPROTO;
	fclose(file);
	if (ret)
		return ret;
	if (!configured)
		return -EIO;
	if (!strcmp(model, "GN25L99"))
		return marker == 0xb0 && writes == 128 ? 0 : -EIO;
	if (!strcmp(model, "GN25L81"))
		return marker == 0xa1 && writes == 256 ? 0 : -EIO;
	if (!strcmp(model, "UX3326S"))
		return marker == 'U' && writes == 514 ? 0 : -EIO;
	if (!strcmp(model, "UX3326"))
		return writes == 56 ? 0 : -EIO;
	return -EOPNOTSUPP;
}

int fttr_miniolt_optical_start(struct fttr_optical_monitor *monitor, int64_t now)
{
	FILE *file = attribute(monitor->directory, "maintenance");
	unsigned int supported, interval;
	int ret;

	if (!file)
		return -errno;
	ret = fscanf(file, "supported=%u interval_ms=%u", &supported, &interval);
	fclose(file);
	if (ret != 2 || supported > 1 || (supported ? interval != 200 && interval != 1000 : interval != 0))
		return -EPROTO;
	monitor->interval = interval;
	monitor->elapsed = 0;
	monitor->due = now;
	monitor->phase = FTTR_OPTICAL_IDLE;
	return 0;
}

int fttr_miniolt_optical_tick(struct fttr_optical_monitor *monitor, int miniolt, int64_t now)
{
	int ret;

	if (!monitor->interval || now < monitor->due)
		return 0;
	switch (monitor->phase) {
	case FTTR_OPTICAL_DISABLED:
		ret = fttr_miniolt_command(miniolt, "tx_enable", "1\n");
		if (ret)
			return ret;
		monitor->phase = FTTR_OPTICAL_ENABLED;
		monitor->due = now + 1000;
		return 0;
	case FTTR_OPTICAL_RELOADED:
	case FTTR_OPTICAL_ENABLED:
		ret = fttr_miniolt_command(monitor->directory, "maintenance", "power\n");
		if (ret == -EUCLEAN && monitor->phase == FTTR_OPTICAL_RELOADED) {
			ret = fttr_miniolt_tx_disable(miniolt);
			if (ret)
				return ret;
			monitor->phase = FTTR_OPTICAL_DISABLED;
			monitor->due = now + 1000;
			return 0;
		}
		if (ret)
			return ret == -EUCLEAN ? -ENOLINK : ret;
		ret = fttr_miniolt_optical_start(monitor, now);
		if (ret)
			return ret;
		break;
	case FTTR_OPTICAL_IDLE:
		if (monitor->elapsed < 1600)
			break;
		monitor->elapsed = 0;
		ret = fttr_miniolt_command(monitor->directory, "maintenance", "reset\n");
		if (ret == -EUCLEAN) {
			ret = fttr_miniolt_command(monitor->directory, "calibration", "1\n");
			if (ret)
				return ret;
			monitor->phase = FTTR_OPTICAL_RELOADED;
			monitor->due = now + 5000;
			return 0;
		}
		if (ret)
			return ret;
		/* The periodic file reload may change the original temperature flag. */
		ret = fttr_miniolt_optical_start(monitor, now);
		if (ret)
			return ret;
		break;
	}
	ret = fttr_miniolt_command(monitor->directory, "maintenance", "temperature\n");
	if (ret)
		return ret;
	monitor->due = now + monitor->interval;
	monitor->elapsed += monitor->interval;
	return 0;
}
