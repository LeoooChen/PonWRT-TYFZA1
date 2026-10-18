// SPDX-License-Identifier: GPL-2.0-or-later
/* CTC PM period/current/history over the native physical-port hardware MIB. */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "pon-pm.h"

static uint16_t get16(const uint8_t *data)
{
	return (uint16_t)data[0] << 8 | data[1];
}

static uint32_t get32(const uint8_t *data)
{
	return (uint32_t)data[0] << 24 | (uint32_t)data[1] << 16 | (uint32_t)data[2] << 8 | data[3];
}

static uint64_t get64(const uint8_t *data)
{
	return (uint64_t)get32(data) << 32 | get32(data + 4);
}

static void put32(uint8_t *data, uint32_t value)
{
	data[0] = value >> 24;
	data[1] = value >> 16;
	data[2] = value >> 8;
	data[3] = value;
}

static uint64_t next_period(const struct pon_pm_port *port, uint64_t now)
{
	/* The original PM clock advances in one-second steps, including when
	 * an OLT configures period0. A Get must not roll that period per request. */
	return now + (uint64_t)(port->period ? port->period : 1) * 1000;
}

void pon_pm_init(struct pon_pm_port ports[PON_PM_PORTS])
{
	memset(ports, 0, sizeof(*ports) * PON_PM_PORTS);
	for (unsigned int i = 0; i < PON_PM_PORTS; i++)
		ports[i].period = 900; /* Actual stock PM default. */
}

int pon_pm_object(const struct pon_oam_request *request)
{
	const uint8_t *object = request->object;
	uint16_t leaf;
	unsigned int port;

	if (!object)
		return -EINVAL;
	leaf = get16(object + 1);
	if (request->object_size == 5 && leaf == 1) {
		port = object[4];
		if (!port)
			return 0;
	} else if (request->object_size == 8 && leaf == 4) {
		return 0;
	} else if (request->object_size == 8 && leaf == 1 && object[4] == 1) {
		port = get16(object + 6);
	} else {
		return -EINVAL;
	}
	return port >= 1 && port < PON_PM_PORTS ? (int)port : -EINVAL;
}

int pon_pm_sample(struct pon_pm_port *port, const char *device, unsigned int index, uint64_t now)
{
	uint8_t record[PON_PM_RECORD_BYTES];
	uint64_t counters[PON_PM_COUNTERS], generation;
	char path[544];
	ssize_t size;
	int fd;

	snprintf(path, sizeof(path), "%s/pm_counters", device);
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -errno;
	size = pread(fd, record, sizeof(record), index * sizeof(record));
	if (size != (ssize_t)sizeof(record)) {
		int ret = size < 0 ? -errno : -EIO;

		close(fd);
		return ret;
	}
	close(fd);
	generation = get64(record);
	for (unsigned int i = 0; i < PON_PM_COUNTERS; i++)
		counters[i] = get64(record + 8 + i * 8);
	if (port->initialized && port->generation != generation) {
		memcpy(port->baseline, counters, sizeof(counters));
		memset(port->history, 0, sizeof(port->history));
		port->next_period = next_period(port, now);
	}
	port->generation = generation;
	port->initialized = true;
	for (unsigned int i = 0; i < PON_PM_COUNTERS; i++)
		port->current[i] = counters[i] - port->baseline[i];
	if (port->enabled && now >= port->next_period) {
		memcpy(port->history, port->current, sizeof(port->history));
		memcpy(port->baseline, counters, sizeof(counters));
		memset(port->current, 0, sizeof(port->current));
		port->next_period = next_period(port, now);
	}
	return 0;
}

int pon_pm_get(struct pon_pm_port *port, uint16_t leaf, uint8_t *output)
{
	const uint64_t *counters = leaf == 0xb2 ? port->current : port->history;

	if (leaf == 0xb1) {
		output[0] = 0;
		output[1] = port->enabled ? 2 : 1;
		put32(output + 2, port->period);
		return 6;
	}
	for (unsigned int i = 0; i < PON_PM_COUNTERS; i++) {
		put32(output + i * 8, counters[i] >> 32);
		put32(output + i * 8 + 4, counters[i]);
	}
	return PON_PM_COUNTERS * 8;
}

int pon_pm_set(struct pon_pm_port *port, uint16_t leaf, const uint8_t *input, size_t size, uint64_t now)
{
	if (leaf == 0xb1) {
		uint16_t state;

		if (size != 6 || ((state = get16(input)) != 1 && state != 2))
			return -EINVAL;
		port->enabled = state == 2;
		port->period = get32(input + 2);
		port->next_period = next_period(port, now);
		return 0;
	}
	/* Reset current period at a real sampled baseline; retained lifetime
	 * counters and other clients' PM state are never cleared in hardware. */
	for (unsigned int i = 0; i < PON_PM_COUNTERS; i++)
		port->baseline[i] += port->current[i];
	memset(port->current, 0, sizeof(port->current));
	memset(port->history, 0, sizeof(port->history));
	port->next_period = next_period(port, now);
	return 0;
}
