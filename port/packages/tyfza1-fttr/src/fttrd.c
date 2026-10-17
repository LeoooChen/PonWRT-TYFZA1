// SPDX-License-Identifier: GPL-2.0-only
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/random.h>
#include <time.h>
#include <unistd.h>

#include "miniolt.h"
#include "omci-mib.h"
#include "omci-socket.h"

enum onu_phase { ONU_ABSENT, ONU_BLOCKED, ONU_MIB, ONU_INVENTORY, ONU_FAILED };

struct onu {
	struct fttr_onu_state hardware;
	struct fttr_mib_job job;
	enum onu_phase phase;
	uint8_t identity[8];
	bool grant;
	FILE *dump;
};

struct controller {
	int directory, ni, store, socket;
	struct fttr_omci_link link;
	const char *interface;
	uint16_t transaction;
	int64_t observed_at;
	bool observed;
	uint64_t rx_errors;
	struct onu onu[FTTR_ONUS];
};

static volatile sig_atomic_t stopping;

static void stop_signal(int signal)
{
	(void)signal;
	stopping = 1;
}

static int64_t now_ms(void)
{
	struct timespec now;

	clock_gettime(CLOCK_MONOTONIC, &now);
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void hex(FILE *file, const uint8_t *data, size_t size)
{
	size_t i;

	for (i = 0; i < size; i++)
		fprintf(file, "%02x", data[i]);
}

static void filename(char name[24], unsigned int id, const char *suffix)
{
	snprintf(name, 24, "onu-%u.%s", id, suffix);
}

static int remove_file(int store, unsigned int id, const char *suffix)
{
	char name[24];

	filename(name, id, suffix);
	return unlinkat(store, name, 0) && errno != ENOENT ? -errno : 0;
}

static int publish(struct controller *c, const char *state, int error)
{
	static const char * const phases[] = { "absent", "blocked", "mib", "inventory", "failed" };
	int fd = openat(c->store, "status.json.new", O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	FILE *file;
	unsigned int id;

	if (fd < 0)
		return -errno;
	file = fdopen(fd, "w");
	if (!file) {
		int ret = -errno;

		close(fd);
		return ret;
	}
	fprintf(file, "{\"state\":\"%s\",\"pid\":%ld,\"error\":%d,\"observed_ms\":%" PRId64 ",\"rx_errors\":%" PRIu64,
		state, stopping || error ? 0L : (long)getpid(), error, c->observed_at, c->rx_errors);
	if (c->observed) {
		fputs(",\"onus\":[", file);
		for (id = 0; id < FTTR_ONUS; id++) {
			struct onu *u = &c->onu[id];

			fprintf(file, "%s{\"id\":%u,\"hardware_registered\":%s,\"phase\":\"%s\","
				"\"error\":%d,\"records\":%u,\"commands\":%u,\"serial\":\"",
				id ? "," : "", id, u->hardware.downstream & (1U << 18) ? "true" : "false",
				phases[u->phase], u->job.error, u->job.sequence, u->job.commands);
			hex(file, u->hardware.serial, 8);
			fputs("\",\"mib_serial\":\"", file);
			hex(file, u->identity, 8);
			fputs("\"}", file);
		}
		fputc(']', file);
	}
	fputs("}\n", file);
	if (fclose(file))
		return -errno;
	return renameat(c->store, "status.json.new", c->store, "status.json") ? -errno : 0;
}

static int cancel(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	int ret;

	if (u->dump) {
		ret = fclose(u->dump);
		u->dump = NULL;
		if (ret)
			return -errno;
	}
	ret = remove_file(c->store, id, "pending");
	if (ret)
		return ret;
	ret = remove_file(c->store, id, "mib");
	if (ret)
		return ret;
	if (u->grant) {
		ret = fttr_miniolt_management(c->directory, id, false);
		if (ret)
			return ret;
		u->grant = false;
	}
	memset(&u->job, 0, sizeof(u->job));
	return 0;
}

static int failed(struct controller *c, unsigned int id, int error)
{
	struct onu *u = &c->onu[id];
	char pending[24], destination[24];

	u->phase = ONU_FAILED;
	u->job.step = FTTR_MIB_FAILED;
	u->job.error = error;
	fprintf(stderr, "FTTR ONU %u MIB failed: %s (%d)\n", id, strerror(-error), error);
	if (u->dump) {
		fprintf(u->dump, "{\"event\":\"mib_error\",\"error\":%d}\n", error);
		if (fclose(u->dump)) {
			u->dump = NULL;
			return -errno;
		}
		u->dump = NULL;
		filename(pending, id, "pending");
		filename(destination, id, "failed");
		if (renameat(c->store, pending, c->store, destination))
			return -errno;
	}
	return 0;
}

static int start_mib(struct controller *c, unsigned int id)
{
	static const uint8_t empty[8];
	struct onu *u = &c->onu[id];
	char name[24];
	int fd, ret;

	memcpy(u->identity, u->hardware.serial, 8);
	if (!memcmp(u->identity, empty, 8))
		return failed(c, id, -EPROTO);
	ret = fttr_miniolt_management(c->directory, id, true);
	if (ret)
		return ret;
	u->grant = true;
	filename(name, id, "pending");
	fd = openat(c->store, name, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	if (fd < 0)
		return -errno;
	u->dump = fdopen(fd, "w");
	if (!u->dump) {
		ret = -errno;
		close(fd);
		return ret;
	}
	fprintf(u->dump, "{\"event\":\"mib_start\",\"id\":%u,\"serial\":\"", id);
	hex(u->dump, u->identity, 8);
	fputs("\"}\n", u->dump);
	u->phase = ONU_MIB;
	fttr_mib_job_start(&u->job, id, &c->transaction);
	return fttr_mib_job_send(&u->job, c->socket, &c->link, now_ms());
}

static int refresh(struct controller *c)
{
	struct fttr_onu_state table[FTTR_ONUS];
	unsigned int id;
	int ret = fttr_miniolt_links_ready(c->directory, c->ni);

	if (ret <= 0)
		return ret ? ret : -ENOLINK;
	ret = fttr_miniolt_table(c->directory, table);
	if (ret)
		return ret;
	c->observed_at = now_ms();
	c->observed = true;
	for (id = 0; id < FTTR_ONUS; id++) {
		struct onu *u = &c->onu[id];
		enum onu_phase inactive = table[id].downstream & (1U << 16) ? ONU_BLOCKED : ONU_ABSENT;

		u->hardware = table[id];
		if (!(table[id].downstream & (1U << 18)) || inactive == ONU_BLOCKED) {
			if (u->phase != inactive) {
				ret = cancel(c, id);
				if (ret)
					return ret;
			}
			u->phase = inactive;
			continue;
		}
		if (u->phase == ONU_ABSENT || u->phase == ONU_BLOCKED || memcmp(u->identity, table[id].serial, 8)) {
			ret = cancel(c, id);
			if (ret)
				return ret;
			ret = start_mib(c, id);
			if (ret)
				return ret;
		}
	}
	return publish(c, "running", 0);
}

static int complete(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct fttr_onu_state current;
	char pending[24], destination[24];
	int ret = fttr_miniolt_one(c->directory, id, &current);

	if (ret)
		return ret;
	if (!(current.downstream & (1U << 18)) || (current.downstream & (1U << 16)) ||
	    memcmp(current.serial, u->identity, 8))
		return failed(c, id, -ESTALE);
	fprintf(u->dump, "{\"event\":\"mib_complete\",\"commands\":%u}\n", u->job.commands);
	ret = fclose(u->dump);
	u->dump = NULL;
	if (ret)
		return -errno;
	filename(pending, id, "pending");
	filename(destination, id, "mib");
	if (renameat(c->store, pending, c->store, destination))
		return -errno;
	u->phase = ONU_INVENTORY;
	return 0;
}

static int receive_message(struct controller *c)
{
	struct fttr_omci_message message;
	struct fttr_mib_record record;
	uint8_t frame[1996];
	struct onu *u;
	unsigned int sequence;
	int ret = fttr_omci_receive(c->socket, c->link.ethertype, frame, sizeof(frame), &message);

	if (ret == -EAGAIN)
		return 0;
	if (ret == -EMSGSIZE || ret == -EBADMSG || ret == -EPROTO || ret == -EOPNOTSUPP) {
		int error = ret;

		c->rx_errors++;
		fprintf(stderr, "FTTR OMCI receive error: %d, ONU=%u\n", error, message.onu);
		if (message.onu < FTTR_ONUS && message.body) {
			u = &c->onu[message.onu];
			if (u->phase == ONU_MIB &&
			    ((uint16_t)message.body[0] << 8 | message.body[1]) == u->job.transaction) {
				ret = failed(c, message.onu, error);
				if (ret)
					return ret;
			}
		}
		return publish(c, "running", 0);
	}
	if (ret)
		return ret;
	if (message.onu >= FTTR_ONUS)
		return 0;
	u = &c->onu[message.onu];
	sequence = u->job.sequence;
	ret = fttr_mib_job_reply(&u->job, &message, &c->transaction, &record);
	if (ret < 0) {
		ret = failed(c, message.onu, ret);
		return ret ? ret : publish(c, "running", 0);
	}
	if (!ret)
		return 0;
	if (ret & FTTR_MIB_RECORD) {
		fprintf(u->dump, "{\"event\":\"mib_record\",\"sequence\":%u,\"class\":%u,"
			"\"instance\":%u,\"mask\":%u,\"attributes\":\"", sequence, record.class_id, record.instance, record.mask);
		hex(u->dump, record.attributes, sizeof(record.attributes));
		fputs("\"}\n", u->dump);
		if (ferror(u->dump))
			return -EIO;
	}
	if (ret & FTTR_MIB_COMPLETE) {
		ret = complete(c, message.onu);
		if (ret)
			return ret;
	} else if (ret & FTTR_MIB_REQUEST) {
		ret = fttr_mib_job_send(&u->job, c->socket, &c->link, now_ms());
		if (ret)
			return ret;
	}
	return publish(c, "running", 0);
}

static int run(struct controller *c)
{
	struct pollfd descriptor = { .fd = c->socket, .events = POLLIN };
	int64_t next_refresh = 0, now, deadline;
	unsigned int id;
	int ret;

	while (!stopping) {
		now = now_ms();
		if (now >= next_refresh) {
			ret = refresh(c);
			if (ret)
				return ret;
			next_refresh = now_ms() + 2000;
		}
		now = now_ms();
		deadline = next_refresh;
		for (id = 0; id < FTTR_ONUS; id++) {
			struct onu *u = &c->onu[id];

			if (u->phase != ONU_MIB)
				continue;
			ret = fttr_mib_job_timeout(&u->job, c->socket, &c->link, now);
			if (ret) {
				if (ret != -ETIMEDOUT)
					return ret;
				ret = failed(c, id, ret);
				if (!ret)
					ret = publish(c, "running", 0);
				if (ret)
					return ret;
			} else if (u->job.deadline < deadline) {
				deadline = u->job.deadline;
			}
		}
		ret = poll(&descriptor, 1, deadline > now ? deadline - now : 0);
		if (ret < 0)
			return -errno;
		if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL))
			return -EIO;
		if (ret) {
			ret = receive_message(c);
			if (ret)
				return ret;
		}
	}
	return 0;
}

static int interface_state(struct controller *c, bool up, int mtu)
{
	struct ifreq request = { 0 };
	int result, fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);

	if (fd < 0)
		return -errno;
	strcpy(request.ifr_name, c->interface);
	if (mtu) {
		request.ifr_mtu = mtu;
		if (ioctl(fd, SIOCSIFMTU, &request)) {
			result = -errno;
			close(fd);
			return result;
		}
	}
	result = ioctl(fd, SIOCGIFFLAGS, &request);
	if (!result) {
		request.ifr_flags = (request.ifr_flags & ~IFF_UP) | (up ? IFF_UP : 0);
		result = ioctl(fd, SIOCSIFFLAGS, &request);
	}
	result = result < 0 ? -errno : 0;
	close(fd);
	return result;
}

static int bootstrap(struct controller *c, int optical, const char *mode, const char *nway, const char *pon)
{
	struct ifreq request = { 0 };
	char setting[16], mac[20];
	const uint8_t *address;
	int fd, ret;

#define BOOT_STEP(name, operation) do { \
	if (stopping) return -EINTR; \
	fprintf(stderr, "FTTR hardware: %s\n", name); \
	ret = (operation); if (ret) return ret; \
} while (0)
	BOOT_STEP("disable TX", fttr_miniolt_tx_disable(c->directory));
	BOOT_STEP("close netdev", interface_state(c, false, 0));
	BOOT_STEP("stop P6", fttr_miniolt_p6_stop(c->ni));
	BOOT_STEP("reset MiniOLT", fttr_miniolt_command(c->directory, "reset", "1\n"));
	BOOT_STEP("calibrate optics", fttr_miniolt_command(optical, "calibration", "1\n"));
	snprintf(setting, sizeof(setting), "%s %s\n", mode, nway);
	BOOT_STEP("configure peer WTG", fttr_miniolt_command(c->directory, "host_serdes", setting));
	BOOT_STEP("configure host P6", fttr_miniolt_command(c->ni, "fttr_link", setting));
	snprintf(setting, sizeof(setting), "%s\n", pon);
	BOOT_STEP("configure PON SerDes", fttr_miniolt_command(c->directory, "pon_serdes", setting));
	BOOT_STEP("wait for PCS", fttr_miniolt_wait_links(c->directory, c->ni));
	BOOT_STEP("initialize core", fttr_miniolt_command(c->directory, "core_init", "1\n"));
	fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return -errno;
	strcpy(request.ifr_name, c->interface);
	ret = ioctl(fd, SIOCGIFHWADDR, &request) ? -errno : 0;
	close(fd);
	if (ret)
		return ret;
	address = (const uint8_t *)request.ifr_hwaddr.sa_data;
	snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x\n",
		address[0], address[1], address[2], address[3], address[4], address[5]);
	BOOT_STEP("configure OMCI envelope", fttr_miniolt_command(c->directory, "omci_link", mac));
	BOOT_STEP("configure DBA", fttr_miniolt_command(c->directory, "dba", "1\n"));
	BOOT_STEP("verify optical calibration", fttr_miniolt_calibrated(optical));
	BOOT_STEP("open netdev", interface_state(c, true, 1982));
	BOOT_STEP("enable TX", fttr_miniolt_command(c->directory, "tx_enable", "1\n"));
#undef BOOT_STEP
	return 0;
}

static int shutdown_hardware(struct controller *c)
{
	int ret = fttr_miniolt_tx_disable(c->directory), result;

	if (ret)
		fprintf(stderr, "FTTR TX disable failed: %d\n", ret);
	result = interface_state(c, false, 0);
	if (result)
		fprintf(stderr, "FTTR netdev close failed: %d\n", result);
	if (!ret)
		ret = result;
	if (c->ni >= 0) {
		result = fttr_miniolt_p6_stop(c->ni);
		if (result)
			fprintf(stderr, "FTTR P6 stop failed: %d\n", result);
		if (!ret)
			ret = result;
	}
	return ret;
}

int main(int argc, char **argv)
{
	struct controller c = { .directory = -1, .ni = -1, .store = -1, .socket = -1 };
	struct sigaction action = { .sa_handler = stop_signal };
	bool owned = false;
	unsigned int id;
	int ret = 0, result;
	uint16_t seed;

	if ((argc != 5 && argc != 9) || strlen(argv[4]) >= IFNAMSIZ) {
		fprintf(stderr, "Usage: %s <MiniOLT-dir> <NI-dir> <state-dir> <interface> [optical-dir host-mode nway pon-mode]\n", argv[0]);
		return 2;
	}
	if (argc == 9 && ((strcmp(argv[6], "5") && strcmp(argv[6], "9")) ||
	    (strcmp(argv[7], "0") && strcmp(argv[7], "1")) ||
	    (strcmp(argv[8], "1") && strcmp(argv[8], "2") && strcmp(argv[8], "3"))))
		return 2;
	c.interface = argv[4];
	sigaction(SIGTERM, &action, NULL);
	sigaction(SIGINT, &action, NULL);
	c.directory = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (c.directory < 0) { ret = -errno; goto out; }
	/* A procd replacement waits for the preceding controller's teardown. */
	if (flock(c.directory, LOCK_EX | (argc == 5 ? LOCK_NB : 0))) { ret = -errno; goto out; }
	owned = true;
	c.ni = open(argv[2], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (c.ni < 0) { ret = -errno; goto out; }
	c.store = open(argv[3], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (c.store < 0) { ret = -errno; goto out; }
	for (id = 0; id < FTTR_ONUS; id++) {
		ret = remove_file(c.store, id, "mib");
		if (!ret)
			ret = remove_file(c.store, id, "pending");
		if (ret)
			goto out;
	}
	if (unlinkat(c.store, "status.json", 0) && errno != ENOENT) { ret = -errno; goto out; }
	if (argc == 9) {
		int optical = open(argv[5], O_RDONLY | O_DIRECTORY | O_CLOEXEC);

		if (optical < 0) { ret = -errno; goto out; }
		ret = publish(&c, "initializing", 0);
		if (!ret)
			ret = bootstrap(&c, optical, argv[6], argv[7], argv[8]);
		close(optical);
		if (ret)
			goto out;
	}
	ret = fttr_miniolt_link(c.directory, &c.link);
	if (ret)
		goto out;
	if (getrandom(&seed, sizeof(seed), 0) < 0) { ret = -errno; goto out; }
	c.transaction = seed | 0x8000;
	c.socket = fttr_omci_open(c.interface, c.link.ethertype);
	if (c.socket < 0) { ret = c.socket; goto out; }
	ret = run(&c);
out:
	if (stopping && ret == -EINTR)
		ret = 0;
	for (id = 0; id < FTTR_ONUS; id++)
		if (c.onu[id].dump && fclose(c.onu[id].dump) && !ret)
			ret = -errno;
	if (owned) {
		result = shutdown_hardware(&c);
		if (!ret)
			ret = result;
	}
	if (c.store >= 0) {
		result = publish(&c, ret ? "failed" : "stopped", ret);
		if (!ret)
			ret = result;
		close(c.store);
	}
	if (c.socket >= 0)
		close(c.socket);
	if (c.ni >= 0)
		close(c.ni);
	if (c.directory >= 0)
		close(c.directory);
	if (ret)
		fprintf(stderr, "FTTR controller stopped: %s (%d)\n", strerror(-ret), ret);
	return ret ? 1 : 0;
}
