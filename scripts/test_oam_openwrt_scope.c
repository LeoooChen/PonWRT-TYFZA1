/* SPDX-License-Identifier: GPL-2.0-or-later */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <linux/ethtool.h>
#include <linux/sockios.h>
#include <net/if.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "pon-backend.h"

static unsigned int writes, signals;

/* Never let a regression change a real interface or signal PID1. */
int __wrap_ioctl(int fd, unsigned long operation, ...)
{
	va_list args;
	struct ifreq *request;

	(void)fd;
	va_start(args, operation);
	request = va_arg(args, struct ifreq *);
	va_end(args);
	assert(!strcmp(request->ifr_name, "lan4"));
	if (operation == SIOCGIFFLAGS) {
		request->ifr_flags = IFF_UP | IFF_RUNNING;
	} else if (operation == SIOCETHTOOL && *(unsigned int *)request->ifr_data == ETHTOOL_GSET) {
		struct ethtool_cmd *settings = (void *)request->ifr_data;
		settings->supported = SUPPORTED_1000baseT_Full;
		settings->advertising = ADVERTISED_1000baseT_Full | ADVERTISED_Pause;
		settings->autoneg = AUTONEG_ENABLE;
		settings->duplex = DUPLEX_FULL;
		ethtool_cmd_speed_set(settings, 1000);
	} else {
		writes++;
	}
	return 0;
}

int __wrap_kill(pid_t pid, int signal_number)
{
	(void)pid;
	(void)signal_number;
	signals++;
	errno = EPERM;
	return -1;
}

static void fixture(const char *directory, const char *name, const char *contents)
{
	char path[512];
	FILE *file;

	snprintf(path, sizeof(path), "%s/%s", directory, name);
	file = fopen(path, "w");
	assert(file && fputs(contents, file) >= 0 && !fclose(file));
}

int main(void)
{
	char directory[] = "/tmp/tyfza1-oam-scope-XXXXXX", path[512];
	struct pon_backend backend = {.epoch = 1};
	struct pon_oam_identity identity = {.mac = {2, 1, 2, 3, 4, 5}};
	uint8_t object[] = {0x37, 0, 1, 4, 1, 0, 0, 1};
	struct pon_oam_request request = {.object = object, .object_size = sizeof(object)};
	uint8_t output[PON_OAM_FRAME_MAX], input[60] = {};
	const uint8_t power[] = {0, 2, 2, 1, 2, 3, 4, 5, 0, 0, 0, 0};
	const struct { uint8_t branch; uint16_t leaf; size_t size; uint8_t data[10]; } blocked[] = {
		{0xc9, 1, 0, {}}, {9, 5, 4, {0, 0, 0, 1}},
		{9, 0xb, 0, {}}, {9, 0xc, 4, {0, 0, 0, 2}},
		{0xc7, 0x12, 1, {1}}, {0xc7, 0x81, 6, {1, 1, 0, 0, 0, 2}},
		{0xc7, 0x82, 10, {1, 1}}, {0xc7, 0xb1, 6, {0, 2, 0, 0, 3, 0x84}},
		{0xc7, 0xb2, 1, {0}},
	};

	assert(mkdtemp(directory));
	snprintf(path, sizeof(path), "%s/mpcp", directory);
	assert(!pon_backend_init(&backend, path));
	pon_backend_bind(&identity, &backend);
	for (size_t i = 0; i < sizeof(blocked) / sizeof(blocked[0]); i++) {
		request.branch = blocked[i].branch;
		request.leaf = blocked[i].leaf;
		assert(identity.set_parameter(&backend, &request, blocked[i].data, blocked[i].size) == 0x87);
	}
	fixture(directory, "epon_tx_control", "unchanged");
	request.branch = 0xc7;
	request.leaf = 0xa1;
	assert(identity.set_parameter(&backend, &request, power, sizeof(power)) == 0x87);
	snprintf(path, sizeof(path), "%s/epon_tx_control", directory);
	FILE *file = fopen(path, "r");
	assert(file && fread(output, 1, 9, file) == 9 && !memcmp(output, "unchanged", 9));
	assert(!fclose(file) && !unlink(path));
	for (unsigned int i = 0; i < 5; i++) {
		const uint16_t leaves[] = {0x81, 0x82, 0xb1, 0xb2, 0xb3};
		request.leaf = leaves[i];
		assert(identity.get_parameter(&backend, &request, output, sizeof(output)) == -EOPNOTSUPP);
	}
	request.branch = 7;
	request.leaf = 0x25;
	assert(identity.get_parameter(&backend, &request, output, sizeof(output)) == 4 && output[3] == 2);
	request.leaf = 0x4f;
	assert(identity.get_parameter(&backend, &request, output, sizeof(output)) == 4 && output[3] == 2);
	request.branch = 0xc7;
	request.leaf = 0x12;
	assert(identity.get_parameter(&backend, &request, output, sizeof(output)) == 1 && output[0] == 1);
	fixture(directory, "llid_count", "1\n");
	request.leaf = 0xc;
	assert(identity.get_parameter(&backend, &request, output, sizeof(output)) == 3 && !output[1]);
	fixture(directory, "transceiver", "temperature_raw=-256 voltage_raw=33000 bias_raw=1234 tx_power_raw=5678 rx_power_raw=65535\n");
	request.leaf = 5;
	assert(identity.get_parameter(&backend, &request, output, sizeof(output)) == 10 && output[0] == 255 && !output[1]);

	/* Reject a descriptor-only reboot without swallowing a following valid Set. */
	struct pon_oam_session session = {.local_echoed = true, .auth = PON_AUTH_ACCEPTED};
	memcpy(input + 12, "\x88\x09\x03", 3);
	input[17] = 0xfe;
	memcpy(input + 18, "\x11\x11\x11\x03\xc9\0\1\xc7\0\10\10\0\0\0\2\0\0\0\xc8", 19);
	assert(pon_oam_receive(&session, &identity, input, sizeof(input), output) == 60);
	assert(!memcmp(output + 22, "\xc9\0\1\x87\xc7\0\10\x80", 8));
	assert(session.auth == PON_AUTH_ACCEPTED);
	memset(input + 21, 0, sizeof(input) - 21);
	input[21] = 0xff;
	input[22] = 2;
	input[24] = 1;
	assert(pon_oam_receive(&session, &identity, input, sizeof(input), output) <= 0);
	assert(!writes && !signals);
	for (size_t i = 0; i < 3; i++) {
		const char *names[] = {"llid_count", "transceiver", "epon_holdover"};
		snprintf(path, sizeof(path), "%s/%s", directory, names[i]);
		assert(!unlink(path));
	}
	assert(!rmdir(directory));
	puts("OpenWrt OAM scope: remote LAN/reboot/TX/alarm/PM denied; read-only state, diagnostics and Holdover retained");
	return 0;
}
