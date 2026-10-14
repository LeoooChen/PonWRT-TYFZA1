// SPDX-License-Identifier: GPL-2.0-or-later
/* Native OpenWrt EPON OAM management; libuci configuration, AF_PACKET transport. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <syslog.h>
#include <time.h>
#include <uci.h>
#include <unistd.h>
#include "pon-oam.h"
#include "pon-backend.h"
#include "pon-config.h"

static volatile sig_atomic_t running = 1;

static void stop(int signal_number)
{
	(void)signal_number;
	running = 0;
}

static uint64_t monotonic_ms(void)
{
	struct timespec time;

	clock_gettime(CLOCK_MONOTONIC, &time);
	return (uint64_t)time.tv_sec * 1000 + time.tv_nsec / 1000000;
}

static int option_copy(struct pon_config *config,
		       const char *key, uint8_t *destination, size_t capacity)
{
	const char *value = pon_config_option(config, key);

	if (!value)
		return 0;
	if (strlen(value) > capacity)
		return -E2BIG;
	memcpy(destination, value, strlen(value));
	return 0;
}

static int identity_load(const char *directory, struct pon_oam_identity *identity)
{
	struct pon_config config = {};
	int ret = pon_config_load(&config, directory);

	if (ret)
		goto done;
#define COPY_OPTION(key, member) do { ret = option_copy(&config, key, identity->member, sizeof(identity->member)); if (ret) goto done; } while (0)
	COPY_OPTION("loid", loid);
	COPY_OPTION("loid_password", password);
	COPY_OPTION("vendor_id", vendor);
	COPY_OPTION("oam_model", model);
	COPY_OPTION("equipment_id", equipment);
	COPY_OPTION("hardware_version", hardware);
	COPY_OPTION("software_version", software);
#undef COPY_OPTION
	identity->loid_configured = identity->loid[0] != 0;
done:
	uci_free_context(config.context);
	return ret;
}

static int registered(const char *path, unsigned int llid, bool *online)
{
	char data[1024], *token, *save = NULL;
	unsigned int bitmap = 0;
	bool enabled = false, have_bitmap = false;
	FILE *stream = fopen(path, "r");

	if (!stream)
		return -errno;
	if (!fgets(data, sizeof(data), stream)) {
		int ret = ferror(stream) ? -EIO : -ENODATA;

		fclose(stream);
		return ret;
	}
	fclose(stream);
	for (token = strtok_r(data, " \n", &save); token; token = strtok_r(NULL, " \n", &save)) {
		if (!strncmp(token, "registered=", 11)) {
			char *end;

			bitmap = strtoul(token + 11, &end, 16);
			have_bitmap = !*end;
		} else if (!strcmp(token, "enabled=1")) {
			enabled = true;
		}
	}
	if (!have_bitmap)
		return -EPROTO;
	*online = enabled && (bitmap & (1U << llid));
	return 0;
}

static int packet_socket(const char *interface, uint8_t mac[6])
{
	struct ifreq request = {};
	struct sockaddr_ll address = {.sll_family = AF_PACKET, .sll_protocol = htons(0x8809)};
	int fd = socket(AF_PACKET, SOCK_RAW | SOCK_CLOEXEC, htons(0x8809));
	int ignore = 1;

	if (fd < 0)
		return -errno;
	strncpy(request.ifr_name, interface, IFNAMSIZ - 1);
	if (ioctl(fd, SIOCGIFINDEX, &request))
		goto error;
	address.sll_ifindex = request.ifr_ifindex;
	if (ioctl(fd, SIOCGIFHWADDR, &request))
		goto error;
	memcpy(mac, request.ifr_hwaddr.sa_data, 6);
	if (setsockopt(fd, SOL_PACKET, PACKET_IGNORE_OUTGOING, &ignore, sizeof(ignore)))
		goto error;
	if (bind(fd, (struct sockaddr *)&address, sizeof(address)))
		goto error;
	return fd;
error:
	ignore = -errno;
	close(fd);
	return ignore;
}

static void read_starttime(char output[32])
{
	char data[1024], *position, *token, *save = NULL;
	unsigned int field = 3;
	FILE *stream = fopen("/proc/self/stat", "r");

	if (!stream)
		return;
	position = fgets(data, sizeof(data), stream) ? strrchr(data, ')') : NULL;
	fclose(stream);
	if (!position)
		return;
	for (token = strtok_r(position + 1, " \n", &save); token; token = strtok_r(NULL, " \n", &save), field++)
		if (field == 22) {
			snprintf(output, 32, "%s", token);
			return;
		}
}

static int publish_status(const char *path, const struct pon_oam_session *session,
			 unsigned int llid, int link_error, bool online, unsigned int send_errors,
			 unsigned int receive_errors, bool loid_configured, uint64_t auth_result_ms,
			 const char *starttime)
{
	static const char *auth[] = {"waiting", "challenge", "accepted", "rejected"};
	char temporary[544];
	FILE *stream;
	int ret;

	snprintf(temporary, sizeof(temporary), "%s.%ld", path, (long)getpid());
	stream = fopen(temporary, "w");
	if (!stream)
		return -errno;
	ret = fprintf(stream,
		"{\"pid\":%ld,\"process_start\":\"%s\",\"updated_ms\":%llu,\"llid_index\":%u,"
		"\"link_error\":%d,\"registered\":%s,\"generation\":%u,\"discovery_stable\":%s,"
		"\"extension_selected\":%s,\"loid_configured\":%s,\"auth_state\":\"%s\","
		"\"auth_failure\":%u,\"auth_challenges\":%u,\"auth_results\":%u,\"auth_successes\":%u,"
		"\"auth_failures\":%u,\"last_auth_result_ms\":%llu,\"rx_frames\":%u,\"tx_frames\":%u,"
		"\"send_errors\":%u,\"receive_errors\":%u}\n",
		(long)getpid(), starttime, (unsigned long long)monotonic_ms(), llid, link_error,
		online ? "true" : "false", session->generation, session->local_echoed ? "true" : "false",
		session->extension_selected ? "true" : "false", loid_configured ? "true" : "false",
		auth[session->auth], session->auth_failure, session->auth_challenges, session->auth_results,
		session->auth_successes, session->auth_failures, (unsigned long long)auth_result_ms,
		session->rx_frames, session->tx_frames, send_errors, receive_errors);
	if (fclose(stream) || ret < 0) {
		unlink(temporary);
		return -EIO;
	}
	if (rename(temporary, path)) {
		ret = -errno;
		unlink(temporary);
		return ret;
	}
	return 0;
}

int main(int argc, char **argv)
{
	const char *interface = "pon-oam0", *configuration = "/etc/config";
	const char *mpcp = "/sys/bus/platform/devices/4f5500000.pon/mpcp";
	const char *state_directory = "/var/run/pon-oam";
	struct pon_oam_identity identity = {};
	struct pon_oam_session session = {};
	struct pon_backend backend = {};
	unsigned int llid = 0, send_errors = 0, receive_errors = 0;
	bool online = false, check_config = false;
	bool session_valid = false;
	int fd, lock_fd, ret, option;
	char state_path[512], lock_path[512], starttime[32] = {};
	uint64_t next_information = 0, next_status = 0, auth_result_ms = 0;
	uint64_t last_received = 0;
	static const struct option options[] = {
		{"interface", required_argument, NULL, 'i'},
		{"llid-index", required_argument, NULL, 'n'},
		{"config-dir", required_argument, NULL, 'c'},
		{"mpcp-status", required_argument, NULL, 'm'},
		{"state-dir", required_argument, NULL, 's'},
		{"check-config", no_argument, NULL, 't'},
		{NULL, 0, NULL, 0}
	};

	while ((option = getopt_long(argc, argv, "i:n:c:m:s:t", options, NULL)) != -1) {
		switch (option) {
		case 'i': interface = optarg; break;
		case 'n': llid = strtoul(optarg, NULL, 10); break;
		case 'c': configuration = optarg; break;
		case 'm': mpcp = optarg; break;
		case 's': state_directory = optarg; break;
		case 't': check_config = true; break;
		default: return 2;
		}
	}
	if (llid >= 32)
		return 2;
	if (pon_backend_init(&backend, mpcp))
		return 2;
	pon_backend_bind(&identity, &backend);
	ret = identity_load(configuration, &identity);
	if (ret) {
		fprintf(stderr, "PON identity configuration failed: %s\n", strerror(-ret));
		return 1;
	}
	if (check_config) {
		printf("{\"loid_configured\":%s,\"loid_bytes\":%zu,\"password_bytes\":%zu,\"hardware_bytes\":%zu,\"software_bytes\":%zu}\n",
			identity.loid_configured ? "true" : "false", strnlen((char *)identity.loid, 24),
			strnlen((char *)identity.password, 12), strnlen((char *)identity.hardware, 16),
			strnlen((char *)identity.software, 16));
		return 0;
	}
	umask(0077);
	if (mkdir(state_directory, 0700) && errno != EEXIST) {
		perror("state directory");
		return 1;
	}
	snprintf(state_path, sizeof(state_path), "%s/llid-%u.json", state_directory, llid);
	snprintf(lock_path, sizeof(lock_path), "%s/llid-%u.lock", state_directory, llid);
	lock_fd = open(lock_path, O_CREAT | O_RDWR | O_CLOEXEC, 0600);
	if (lock_fd < 0 || flock(lock_fd, LOCK_EX | LOCK_NB)) {
		perror("OAM instance lock");
		return 1;
	}
	fd = packet_socket(interface, identity.mac);
	if (fd < 0) {
		fprintf(stderr, "OAM interface %s: %s\n", interface, strerror(-fd));
		return 1;
	}
	read_starttime(starttime);
	signal(SIGTERM, stop);
	signal(SIGINT, stop);
	openlog("pon-oamd", LOG_PID, LOG_DAEMON);
	pon_oam_reset(&session);
	while (running) {
		uint8_t input[PON_OAM_FRAME_MAX], output[PON_OAM_FRAME_MAX];
		struct timeval timeout = {.tv_usec = 200000};
		fd_set readable;
		bool now_online = false;
		uint64_t now = monotonic_ms();
		int link_error = registered(mpcp, llid, &now_online);

		if (link_error)
			now_online = false;
		if (now_online != online) {
			pon_oam_reset(&session);
			session_valid = false;
			last_received = 0;
			online = now_online;
			next_information = now;
			syslog(LOG_INFO, "LLID%u MPCP registered=%u generation=%u", llid, online, session.generation);
		}
		if (online && session_valid && now - last_received >= 5000) {
			pon_oam_reset(&session);
			session_valid = false;
			syslog(LOG_INFO, "LLID%u OAM peer timeout generation=%u", llid, session.generation);
		}
		if (online && now >= next_information) {
			size_t size = pon_oam_information(&session, &identity, output);

			if (send(fd, output, size, 0) == (ssize_t)size)
				pon_oam_information_sent(&session, output);
			else
				send_errors++;
			next_information = now + 1000;
		}
		if (now >= next_status) {
			ret = publish_status(state_path, &session, llid, link_error, online, send_errors,
				receive_errors, identity.loid_configured, auth_result_ms, starttime);
			if (ret)
				syslog(LOG_ERR, "LLID%u status publish failed: %s", llid, strerror(-ret));
			next_status = now + 1000;
		}
		FD_ZERO(&readable);
		FD_SET(fd, &readable);
		ret = select(fd + 1, &readable, NULL, NULL, &timeout);
		if (ret < 0) {
			if (errno == EINTR)
				continue;
			syslog(LOG_ERR, "OAM select failed: %s", strerror(errno));
			break;
		}
		if (ret && FD_ISSET(fd, &readable)) {
			ssize_t size = recv(fd, input, sizeof(input), MSG_DONTWAIT);
			unsigned int results_before = session.auth_results;

			if (size < 0) {
				receive_errors++;
				continue;
			}
			if (!online)
				continue;
			ret = pon_oam_receive(&session, &identity, input, size, output);
			if (ret < 0) {
				receive_errors++;
				continue;
			}
			if (ret == PON_OAM_IGNORED)
				continue;
			last_received = monotonic_ms();
			session_valid = true;
			if (ret > PON_OAM_HANDLED) {
				if (send(fd, output, ret, 0) == ret)
					session.tx_frames++;
				else
					send_errors++;
			} else if (size >= 18 && input[17] == 0) {
				next_information = monotonic_ms();
			}
			if (session.auth_results != results_before) {
				auth_result_ms = monotonic_ms();
				syslog(LOG_INFO, "LLID%u subscriber_auth result=%u failure=%u generation=%u",
					llid, session.auth, session.auth_failure, session.generation);
			}
			next_status = 0;
		}
	}
	pon_oam_reset(&session);
	unlink(state_path);
	close(fd);
	close(lock_fd);
	closelog();
	return running ? 1 : 0;
}
