// SPDX-License-Identifier: GPL-2.0-only
/* OMCI transport and UCI identity for the native RTL9617C ITU backend. */
#include <arpa/inet.h>
#include <errno.h>
#include <ctype.h>
#include <fcntl.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>
#include "pon-config.h"
#include "pon-omci.h"

#define PON_SYSFS "/sys/bus/platform/devices/4f5500000.pon/"
#define PATH_BYTES (12 + 248 * 50 + 248 * 5 * (32 + 32 * 16))

static int put_file(const char *path, const void *data, size_t length)
{
	int fd = open(path, O_WRONLY | O_CLOEXEC), result;
	ssize_t written;
	size_t offset = 0;

	if (fd < 0) return -errno;
	result = 0;
	while (offset < length) {
		written = write(fd, (const uint8_t *)data + offset, length - offset);
		if (written <= 0) { result = written < 0 ? -errno : -EIO; break; }
		offset += written;
	}
	close(fd);
	return result;
}

static void identity_field(struct pon_config *config, const char *name, const char *default_value,
	uint8_t *output, size_t width)
{
	const char *value = pon_config_option(config, name);

	if (!value || !value[0]) value = default_value;
	memcpy(output, value, strnlen(value, width));
}

static int load_identity(struct omci_identity *identity)
{
	struct pon_config config = {};
	FILE *serial;
	unsigned int i;
	int result = pon_config_load(&config, "/etc/config");

	if (result) goto out;
	serial = fopen(PON_SYSFS "active_serial_number", "r");
	if (!serial) { result = -errno; goto out; }
	for (i = 0; i < 8; i++)
		if (fscanf(serial, "%2hhx", &identity->serial[i]) != 1) { result = -EINVAL; break; }
	fclose(serial);
	identity_field(&config, "equipment_id", "TYFZA1", identity->equipment, sizeof(identity->equipment));
	identity_field(&config, "hardware_version", "RTL9617C", identity->hardware, sizeof(identity->hardware));
	identity_field(&config, "software_version", "0.2.0-alpha", identity->software, sizeof(identity->software));
	identity_field(&config, "loid", "", identity->loid, sizeof(identity->loid));
	identity_field(&config, "loid_password", "", identity->password, sizeof(identity->password));
	identity_field(&config, "operator_id", "CTC", identity->operator_id, sizeof(identity->operator_id));
	const char *psk = pon_config_option(&config, "omci_psk");
	if (psk && psk[0]) {
		if (strlen(psk) != 32) { result = -EINVAL; goto out; }
		for (i = 0; i < 32; i++) if (!isxdigit((unsigned char)psk[i])) { result = -EINVAL; goto out; }
		for (i = 0; i < 16; i++)
			if (sscanf(psk + i * 2, "%2hhx", &identity->psk[i]) != 1) { result = -EINVAL; goto out; }
		identity->has_psk = 1;
	}
out:
	uci_free_context(config.context);
	return result;
}

static unsigned int paths_configured(unsigned long long *session)
{
	FILE *status = fopen(PON_SYSFS "data_path", "r");
	unsigned int ready = 0, count;
	unsigned long long epoch;
	int error;

	if (!status) return 0;
	if (fscanf(status, "configured=%u error=%d epoch=%llu paths=%u session=%llu",
	    &ready, &error, &epoch, &count, session) != 5) ready = 0;
	fclose(status);
	return ready;
}

struct omci_business {
	uint8_t applied[PATH_BYTES];
	int length, error;
};

static int read_starttime(char output[32])
{
	char data[1024], *position, *token, *save = NULL;
	unsigned int field = 3;
	FILE *stream = fopen("/proc/self/stat", "r");

	if (!stream) return -errno;
	position = fgets(data, sizeof(data), stream) ? strrchr(data, ')') : NULL;
	fclose(stream);
	if (!position) return -EINVAL;
	for (token = strtok_r(position + 1, " \n", &save); token;
	     token = strtok_r(NULL, " \n", &save), field++) {
		if (field == 22) {
			snprintf(output, 32, "%s", token);
			return 0;
		}
	}
	return -EINVAL;
}

static int publish_status(const char *path, const char *starttime,
	unsigned long long received, unsigned long long transmitted, unsigned long long invalid,
	const struct omci_business *business)
{
	struct timespec now;
	char temporary[512];
	FILE *stream;
	int error = 0;

	clock_gettime(CLOCK_BOOTTIME, &now);
	snprintf(temporary, sizeof(temporary), "%s.%ld", path, (long)getpid());
	stream = fopen(temporary, "w");
	if (!stream) return -errno;
	if (fprintf(stream, "pid=%ld process_start=%s updated_ms=%llu rx=%llu tx=%llu invalid=%llu paths=%u error=%d\n",
	    (long)getpid(), starttime, (unsigned long long)now.tv_sec * 1000 + now.tv_nsec / 1000000,
	    received, transmitted, invalid,
	    business->length >= 12 ? business->applied[2] | (unsigned int)business->applied[3] << 8 : 0,
	    business->error) < 0) error = errno;
	if (fclose(stream) && !error) error = errno;
	if (!error && rename(temporary, path)) error = errno;
	if (error) unlink(temporary);
	return -error;
}

static int apply_paths(void *context, const uint8_t *paths, size_t length)
{
	struct omci_business *business = context;
	unsigned long long session;
	unsigned int ready = paths_configured(&session);
	int result = 0;

	if (length != (size_t)business->length || memcmp(paths, business->applied, length) ||
	    ((paths[2] || paths[3]) && !ready)) {
		result = put_file(PON_SYSFS "itu_paths", paths, length);
		if (!result) {
			memcpy(business->applied, paths, length);
			business->length = length;
		} else if (result != -EAGAIN) fprintf(stderr, "OMCI business mapping: %s\n", strerror(-result));
	}
	business->error = result;
	return result;
}

int main(void)
{
	struct omci_identity identity = {};
	struct omci_agent *agent;
	struct sockaddr_ll address = {.sll_family = AF_PACKET, .sll_protocol = htons(ETH_P_ALL)};
	uint8_t input[2048], output[2048], key[16];
	static uint8_t paths[PATH_BYTES];
	static struct omci_business business;
	unsigned long long session = 0, previous_session = 0, received = 0, transmitted = 0, invalid = 0;
	int fd, enabled = 1, result;
	struct pollfd pollfd;
	char starttime[32];

	openlog("pon-omcid", LOG_PID, LOG_DAEMON);
	result = read_starttime(starttime);
	if (result) { fprintf(stderr, "OMCI process identity: %s\n", strerror(-result)); return 1; }
	result = load_identity(&identity);
	if (result) { fprintf(stderr, "OMCI identity: %s\n", strerror(-result)); return 1; }
	agent = tyfza1_omci_new(&identity);
	memset(&identity, 0, sizeof(identity));
	fd = socket(AF_PACKET, SOCK_RAW | SOCK_CLOEXEC, htons(ETH_P_ALL));
	if (fd < 0) { perror("OMCI socket"); tyfza1_omci_free(agent); return 1; }
	address.sll_ifindex = if_nametoindex("pon-omci");
	if (!address.sll_ifindex) { perror("pon-omci"); close(fd); tyfza1_omci_free(agent); return 1; }
	if (bind(fd, (struct sockaddr *)&address, sizeof(address))) {
		perror("OMCI bind"); close(fd); tyfza1_omci_free(agent); return 1;
	}
	setsockopt(fd, SOL_PACKET, PACKET_IGNORE_OUTGOING, &enabled, sizeof(enabled));
	pollfd = (struct pollfd){.fd = fd, .events = POLLIN};
	for (;;) {
		paths_configured(&session);
		int length;

		if (session != previous_session) {
			tyfza1_omci_epoch(agent);
			previous_session = session;
		}
		length = tyfza1_omci_paths(agent, paths);
		if (length < 0) {
			/* A rejected graph must not leave its previous business mapping active. */
			static const uint8_t empty[12] = {3, 0, 0, 0, 0, 0, 0, 0, 12};
			result = business.length ? apply_paths(&business, empty, sizeof(empty)) : 0;
			if (!result) business.length = 0;
			business.error = result ? result : length;
		} else if (length >= 12) apply_paths(&business, paths, length);
		result = publish_status("/var/run/pon-omci", starttime, received, transmitted, invalid, &business);
		if (result) syslog(LOG_ERR, "OMCI status publish failed: %s", strerror(-result));
		result = poll(&pollfd, 1, 1000);
		if (result < 0) { if (errno == EINTR) continue; break; }
		if (!result) continue;
		length = recv(fd, input, sizeof(input), 0);
		if (length < 0) { if (errno == EINTR || errno == ENETDOWN) continue; break; }
		received++;
		uint8_t broadcast[18];
		bool security_set = length >= 12 && (input[2] & 31) == 8 && input[4] == 1 && input[5] == 0x4c;
		unsigned int mask_offset = input[3] == 0x0b ? 10 : 8;
		struct timespec security_received, security_replied;
		int backend_result = 0;
		if (security_set) clock_gettime(CLOCK_MONOTONIC, &security_received);
		if (tyfza1_omci_broadcast(agent, input, length, broadcast))
			backend_result = put_file(PON_SYSFS "omci_broadcast", broadcast, sizeof(broadcast));
		length = tyfza1_omci_dispatch_apply(agent, input, length, output, &backend_result,
			apply_paths, &business);
		if (backend_result < 0) business.error = backend_result;
		if (length < 0) { invalid++; continue; }
		result = send(fd, output, length, 0);
		int send_error = result < 0 ? errno : 0;
		if (security_set) {
			clock_gettime(CLOCK_MONOTONIC, &security_replied);
			syslog(LOG_INFO, "Class332 SET rx=%lld.%09ld tx=%lld.%09ld tid=%02x%02x mask=%02x%02x backend=%d response=%u sent=%d",
				(long long)security_received.tv_sec, security_received.tv_nsec,
				(long long)security_replied.tv_sec, security_replied.tv_nsec,
				input[0], input[1], input[mask_offset], input[mask_offset + 1],
				backend_result, output[output[3] == 0x0b ? 10 : 8], send_error ? -send_error : result);
		}
		if (result < 0) { if (send_error == ENETDOWN) continue; break; }
		transmitted++;
		/* The response uses the current IK; a completed Class332 exchange installs the next MSK. */
		if (tyfza1_omci_session_key(agent, key)) {
			char text[33];
			for (unsigned int i = 0; i < 16; i++) snprintf(text + i * 2, 3, "%02x", key[i]);
			result = put_file(PON_SYSFS "omci_msk", text, 32);
			clock_gettime(CLOCK_MONOTONIC, &security_replied);
			syslog(LOG_INFO, "Class332 MSK time=%lld.%09ld result=%d",
				(long long)security_replied.tv_sec, security_replied.tv_nsec, result);
			tyfza1_omci_key_complete(agent, !result);
			memset(key, 0, sizeof(key));
		}
		while ((length = tyfza1_omci_notification(agent, output)) > 0) {
			if (send(fd, output, length, 0) < 0) break;
			transmitted++;
		}
	}
	perror("OMCI transport");
	close(fd);
	tyfza1_omci_free(agent);
	unlink("/var/run/pon-omci");
	return 1;
}
