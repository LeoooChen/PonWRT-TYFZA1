// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
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
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "miniolt.h"
#include "fttr-net.h"
#include "fttr-control.h"
#include "omci-mib.h"
#include "omci-model.h"
#include "omci-services.h"
#include "omci-wifi.h"
#include "omci-auth.h"
#include "omci-upgrade.h"
#include "onu-policy.h"
#include "omci-socket.h"

enum onu_phase { ONU_ABSENT, ONU_BLOCKED, ONU_MIB, ONU_INVENTORY, ONU_FAILED,
	ONU_SERVICE, ONU_CONFIGURED, ONU_SERVICE_FAILED, ONU_SYNC, ONU_WIFI, ONU_AUTH, ONU_AUTH_FAILED, ONU_MOVE, ONU_REBOOT, ONU_REBOOT_WAIT, ONU_UPGRADE, ONU_UPGRADE_WAIT };

struct wifi_policy {
	struct wifi_policy *next;
	struct fttr_wifi_input input;
	uint8_t serial[8];
	bool wildcard;
};

struct wifi_run {
	struct wifi_run *next;
	struct fttr_wifi_job job;
};

struct mib_sync {
	struct fttr_me_job job, failure;
	int64_t due, checked;
	int error;
	uint8_t value, observed;
	bool enabled, active, valid, recover, gem_probe;
	struct wifi_run *wifi_probe;
	int wifi_group;
	int mcast_step;
	bool mcast_absent;
	unsigned int service_probe;
};

struct service_policy {
	struct service_policy *next;
	struct fttr_service_config config;
	uint16_t ordinal;
	uint8_t serial[8];
	char bridge[IFNAMSIZ];
	bool wildcard;
};

struct onu {
	struct fttr_onu_state hardware;
	struct fttr_mib_job job;
	struct fttr_mib_model model;
	struct fttr_services services;
	const struct service_policy *selected[FTTR_SERVICES];
	struct fttr_auth_job auth;
	struct fttr_onu_selection selection;
	bool policy_authenticated, policy_move;
	int policy_error;
	int64_t auth_retry;
	struct wifi_run *wifi, *wifi_current;
	unsigned int wifi_count, wifi_configured;
	int wifi_error, wifi_cleanup_error;
	bool service_started, service_ready, multicast_absent;
	struct mib_sync sync;
	struct fttr_net_port ports[FTTR_SERVICES];
	int datapath_error, datapath_cleanup_error;
	unsigned int rescans;
	enum onu_phase phase;
	uint8_t identity[8];
	bool grant;
	struct fttr_upgrade_job upgrade;
	struct fttr_me_job reboot;
	int reboot_error;
	bool admin_pending;
	uint8_t admin_serial[8];
	int64_t admin_timeout;
	FILE *dump;
};

struct controller {
	int directory, ni, store, socket, control;
	struct fttr_omci_link link;
	enum fttr_backend backend;
	struct fttr_net net;
	struct fttr_net_port multicast;
	unsigned int multicast_members;
	bool multicast_enabled;
	int multicast_error;
	int64_t multicast_checked;
	struct fttr_optical_monitor optical;
	struct fttr_auth_config auth;
	struct fttr_onu_policy onu_policy;
	const char *interface;
	uint16_t transaction;
	int64_t observed_at;
	int64_t admin_scan_due;
	bool observed;
	uint64_t rx_errors;
	struct service_policy *policies;
	unsigned int policy_count;
	struct wifi_policy *wifi_policies;
	unsigned int wifi_policy_count;
	struct onu onu[FTTR_ONUS];
	struct {
		bool active;
		struct fttr_admin_request request;
		struct sockaddr_un client;
		socklen_t client_length;
	} move, reboot, upgrade;
};

static int multicast_close(struct controller *c)
{
	int ret = fttr_net_mcast_remove(&c->net, &c->multicast);

	/* Initial forwarding withdrawal precedes netlink takeover of an inherited
	 * upper. fttr_forward already withdraws its FE; acknowledge the separate
	 * multicast close once netlink has actually closed that upper. */
	if (!ret && c->net.fd >= 0)
		ret = fttr_miniolt_command(c->ni, "fttr_mcast_flush", "1\n");
	return ret;
}

static int forwarding(struct controller *c, bool enable)
{
	char command[24];
	int ret;

	if (!enable) {
		ret = multicast_close(c);
		if (ret)
			return ret;
		c->multicast_members = 0;
		return fttr_miniolt_command(c->ni, "fttr_forward", "0\n");
	}
	/* Another ONU may finish cleanup while a disruptive command is pending. */
	if (c->upgrade.active || c->reboot.active) return 0;
	snprintf(command, sizeof(command), "1 %04x\n", c->link.ethertype);
	return fttr_miniolt_command(c->ni, "fttr_forward", command);
}

static int move_drive(struct controller *c);
static int reboot_drive(struct controller *c);
static int upgrade_drive(struct controller *c);
static int upgrade_finish(struct controller *c, int result);
static int reboot_finish(struct controller *c, int result);
static int policy_move(struct controller *c, unsigned int id);
static int auth_drive(struct controller *c, unsigned int id);

static bool authenticated(const struct controller *c, const struct onu *u)
{
	return (!c->auth.enabled || (u->auth.phase == FTTR_AUTH_DONE && u->auth.verified && !u->auth.error)) &&
		(!c->onu_policy.enabled || u->policy_authenticated);
}
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

static void filename(char name[24], uint8_t id, const char *suffix)
{
	snprintf(name, 24, "onu-%u.%s", (unsigned int)id, suffix);
}

/* Policy chooses a UNI ordinal of a stated type; its ME ID comes from the MIB. */
static int load_policies(struct controller *c, const char *path)
{
	FILE *file = fopen(path, "r");
	char *line = NULL;
	size_t capacity = 0;
	ssize_t length;
	unsigned int number = 0;
	int ret = 0;

	if (!file)
		return -errno;
	while ((length = getline(&line, &capacity, file)) >= 0) {
		struct service_policy *policy, *other;
		char *token[11], *save, *p, *end;
		unsigned long value[8];
		unsigned int n = 0, i, total = 0;
		bool factory;
		bool tagged = false, filter_only = false;

		number++;
		if (memchr(line, '\0', length)) { ret = -EINVAL; break; }
		for (p = strtok_r(line, " \t\r\n", &save); p && n < 11;
		     p = strtok_r(NULL, " \t\r\n", &save)) {
			if (*p == '#')
				break;
			token[n++] = p;
		}
		if (!n)
			continue;
		factory = n >= 2 && (!strcmp(token[1], "factory") || !strcmp(token[1], "factory-ctc"));
		if (factory && n == 4) {
			errno = 0;
			value[0] = strtoul(token[2], &end, 10);
			if (errno || !*token[2] || *end || value[0] < 2 || value[0] > 8) { ret = -EINVAL; break; }
			total = value[0];
			token[9] = token[3];
		}
		if ((factory ? n != 4 : n != 10) || !*token[9] || strlen(token[9]) >= IFNAMSIZ ||
		    strchr(token[9], '/') || strchr(token[9], ':') || strchr(token[9], '"') ||
		    strchr(token[9], '\\')) { ret = -EINVAL; break; }
		for (i = 0; !factory && i < 8; i++) {
			if (!i && (!strcmp(token[1], "shared") || !strcmp(token[1], "shared-mcast") || !strcmp(token[1], "shared-ctc"))) {
				value[0] = !strcmp(token[1], "shared") ? 3 : !strcmp(token[1], "shared-mcast") ? 4 : 5;
				continue;
			}
			if (i == 4) {
				uint16_t vid;

				ret = fttr_service_vlan_parse(token[i + 1], &tagged, &filter_only, &vid);
				if (ret) break;
				value[i] = vid;
				continue;
			}
			errno = 0;
			value[i] = strtoul(token[i + 1], &end, 0);
			if (errno || *end || value[i] > UINT16_MAX) { ret = -EINVAL; break; }
		}
		if (ret)
			break;
		if (!factory && ((value[0] != 0 && value[0] != 2 && value[0] != 3 && value[0] != 4 && value[0] != 5) ||
		    (value[0] >= 3 && (strncmp(token[1], "shared", 6) || value[1])) || !value[2] || value[2] > 7 ||
		    value[3] > 7 || value[4] > 4094)) { ret = -EINVAL; break; }
		policy = calloc(1, sizeof(*policy));
		if (!policy) { ret = -ENOMEM; break; }
		policy->wildcard = !strcmp(token[0], "*");
		if (!policy->wildcard) {
			if (strlen(token[0]) != 16)
				ret = -EINVAL;
			for (i = 0; !ret && i < 16; i++)
				if (!isxdigit((unsigned char)token[0][i]))
					ret = -EINVAL;
			for (i = 0; !ret && i < 8; i++) {
				char byte[3] = { token[0][2 * i], token[0][2 * i + 1], 0 };
				unsigned long v = strtoul(byte, &end, 16);

				if (end != byte + 2)
					ret = -EINVAL;
				policy->serial[i] = v;
			}
		}
		for (other = c->policies; other && !ret; other = other->next)
			if (policy->wildcard == other->wildcard &&
			    (policy->wildcard || !memcmp(policy->serial, other->serial, 8)) &&
			    (factory || other->config.factory || other->config.slot == value[2]))
				ret = -EEXIST;
		if (ret) { free(policy); break; }
		if (factory) {
			for (i = 1; i < total; i++) {
				struct service_policy *row = i == 1 ? policy : calloc(1, sizeof(*row));
				if (!row) { ret = -ENOMEM; break; }
				if (i != 1) { row->wildcard = policy->wildcard; memcpy(row->serial, policy->serial, 8); }
				fttr_service_default(&row->config, total, i);
				if (row->config.multicast && !strcmp(token[1], "factory-ctc")) row->config.multicast = 2;
				if (strcmp(token[9], "-")) strcpy(row->bridge, token[9]);
				if (row->config.multicast && row->bridge[0]) c->multicast_enabled = true;
				row->next = c->policies; c->policies = row; c->policy_count++;
			}
			if (ret) break;
			continue;
		}
		policy->config = (struct fttr_service_config) {
			.veip = value[0] == 2, .shared = value[0] >= 3,
			.multicast = value[0] >= 4 ? value[0] - 3 : 0, .slot = value[2], .priority = value[3],
			.tagged = tagged, .filter_only = filter_only, .vid = value[4],
			.fixed = value[5], .assured = value[6], .maximum = value[7]
		};
		if (policy->config.multicast && strcmp(token[9], "-"))
			c->multicast_enabled = true;
		policy->ordinal = value[1];
		if (strcmp(token[9], "-"))
			strcpy(policy->bridge, token[9]);
		policy->next = c->policies;
		c->policies = policy;
		c->policy_count++;
	}
	/* GEM4095 is one downstream broadcast domain. Two local bridges cannot
	 * share it without mixing traffic at the subordinate GEM filters.
	 */
	if (!ret) {
		const struct service_policy *policy, *other;

		for (policy = c->policies; policy; policy = policy->next)
			for (other = policy->next; other; other = other->next) {
				if (policy->config.multicast && other->config.multicast &&
				    strcmp(policy->bridge, other->bridge))
					ret = -EXDEV;
				if (policy->wildcard != other->wildcard ||
				    (!policy->wildcard && memcmp(policy->serial, other->serial, 8))) continue;
				if (!policy->config.shared || !other->config.shared ||
				    !policy->config.filter_only || !other->config.filter_only ||
				    policy->config.factory != other->config.factory ||
				    policy->config.vid == other->config.vid ||
				    (policy->config.multicast && other->config.multicast))
					ret = -EINVAL;
			}
	}
	if (!ret && ferror(file))
		ret = -EIO;
	if (ret)
		fprintf(stderr, "FTTR policy %s:%u: %s (%d)\n", path, number, strerror(-ret), ret);
	free(line);
	fclose(file);
	return ret;
}

static int load_wifi_policies(struct controller *c, const char *path)
{
	FILE *file = fopen(path, "r");
	char *line = NULL;
	size_t capacity = 0;
	ssize_t length;
	unsigned int number = 0;
	int ret = 0;

	if (!file)
		return -errno;
	while ((length = getline(&line, &capacity, file)) >= 0) {
		struct wifi_policy *policy, **tail;
		char *serial, *config, *extra, *save;
		unsigned int i;

		number++;
		if (memchr(line, '\0', length)) { ret = -EINVAL; break; }
		serial = strtok_r(line, " \t\r\n", &save);
		if (!serial || *serial == '#')
			continue;
		config = strtok_r(NULL, " \t\r\n", &save);
		extra = strtok_r(NULL, " \t\r\n", &save);
		if (!config || *config != '/' || (extra && *extra != '#')) { ret = -EINVAL; break; }
		policy = calloc(1, sizeof(*policy));
		if (!policy) { ret = -ENOMEM; break; }
		policy->wildcard = !strcmp(serial, "*");
		if (!policy->wildcard) {
			if (strlen(serial) != 16)
				ret = -EINVAL;
			for (i = 0; !ret && i < 16; i++)
				if (!isxdigit((unsigned char)serial[i]))
					ret = -EINVAL;
			for (i = 0; !ret && i < 8; i++) {
				char byte[3] = { serial[i * 2], serial[i * 2 + 1], 0 };

				policy->serial[i] = strtoul(byte, NULL, 16);
			}
		}
		if (!ret)
			ret = fttr_wifi_config_read(config, &policy->input);
		for (tail = &c->wifi_policies; *tail && !ret; tail = &(*tail)->next)
			if ((*tail)->wildcard == policy->wildcard &&
			    (policy->wildcard || !memcmp((*tail)->serial, policy->serial, 8)) &&
			    (*tail)->input.config.instance == policy->input.config.instance)
				ret = -EEXIST;
		if (ret) { free(policy); break; }
		*tail = policy;
		c->wifi_policy_count++;
	}
	if (!ret && ferror(file))
		ret = -EIO;
	if (ret)
		fprintf(stderr, "FTTR Wi-Fi policy %s:%u: %s (%d)\n", path, number, strerror(-ret), ret);
	free(line);
	fclose(file);
	return ret;
}

static int remove_file(int store, unsigned int id, const char *suffix)
{
	char name[24];

	filename(name, id, suffix);
	return unlinkat(store, name, 0) && errno != ENOENT ? -errno : 0;
}

static bool datapath_configured(const struct onu *u)
{
	unsigned int n;
	bool any = false;
	for (n = 0; n < u->services.count; n++) {
		if (!u->selected[n] || !*u->selected[n]->bridge) continue;
		any = true;
		if (!u->ports[n].configured) return false;
	}
	return any;
}

static struct fttr_service_job *multicast_service(struct onu *u)
{
	unsigned int n;
	for (n = 0; n < u->services.count; n++)
		if (u->services.job[n].config.multicast) return &u->services.job[n];
	return NULL;
}

static int publish(struct controller *c, const char *state, int error)
{
	static const char * const phases[] = { "absent", "blocked", "mib", "inventory", "failed",
		"provision", "configured", "service-failed", "sync", "wifi", "authentication", "auth-failed", "move-release", "reboot", "reboot-wait", "upgrade", "upgrade-wait" };
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
	fprintf(file, "{\"state\":\"%s\",\"pid\":%ld,\"error\":%d,\"observed_ms\":%" PRId64
		",\"rx_errors\":%" PRIu64 ",\"service_policies\":%u,\"wifi_policies\":%u",
		state, stopping || error ? 0L : (long)getpid(), error, c->observed_at, c->rx_errors, c->policy_count, c->wifi_policy_count);
	fprintf(file, ",\"multicast\":{\"configured\":%s,\"members\":%u,\"error\":%d,\"bridge\":\"%s\"}",
		c->multicast.configured ? "true" : "false", c->multicast_members, c->multicast_error, c->multicast.bridge);
	if (c->observed) {
		fputs(",\"onus\":[", file);
		for (id = 0; id < FTTR_ONUS; id++) {
			struct onu *u = &c->onu[id];

			fprintf(file, "%s{\"id\":%u,\"hardware_registered\":%s,\"phase\":\"%s\","
				"\"error\":%d,\"records\":%u,\"commands\":%u,\"entities\":%u,"
				"\"unparsed_records\":%u,\"serial\":\"",
				id ? "," : "", id, u->hardware.downstream & (1U << 18) ? "true" : "false",
				phases[u->phase], u->service_started ? u->services.error : u->job.error, u->job.sequence, u->job.commands,
				u->model.count, u->model.unparsed_records);
			hex(file, u->hardware.serial, 8);
			fputs("\",\"mib_serial\":\"", file);
			hex(file, u->identity, 8);
			fprintf(file, "\",\"service_count\":%u,\"service_configured\":%s,\"service_cleanup_error\":%d,"
				"\"mib_sync_valid\":%s,\"mib_sync\":%u,\"mib_sync_observed\":%u,"
				"\"mib_sync_error\":%d,\"mib_sync_checked_ms\":%" PRId64 ",\"mib_rescans\":%u,"
				"\"datapath_configured\":%s,\"datapath_error\":%d,\"datapath_cleanup_error\":%d,"
				"\"wifi_instances\":%u,\"wifi_configured\":%u,\"wifi_error\":%d,\"wifi_cleanup_error\":%d,"
				"\"temporary_deactivation\":%s,\"loid_auth_required\":%s,"
				"\"loid_acquired\":%s,\"loid_result\":%u,\"loid_result_verified\":%s,"
				"\"loid_authenticated\":%s,\"loid_auth_error\":%d,\"loid_fallback_error\":%d,"
				"\"onu_auth_required\":%s,\"onu_authenticated\":%s,\"onu_auth_action\":%u,"
				"\"onu_auth_logical_id\":%u,\"onu_auth_method\":%u,\"onu_auth_error\":%d,"
				"\"reboot_sent\":%s,\"reboot_accepted\":%s,\"reboot_error\":%d,\"upgrade_stage\":%u,"
				"\"upgrade_bytes\":%u,\"upgrade_size\":%u,\"upgrade_action\":%u,\"upgrade_image\":%u,\"upgrade_accepted\":%s,\"upgrade_verified\":%s,\"upgrade_error\":%d}",
				u->services.count, u->services.configured ? "true" : "false", u->services.cleanup_error,
				u->sync.valid ? "true" : "false", u->sync.value, u->sync.observed,
				u->sync.error, u->sync.checked, u->rescans,
				datapath_configured(u) ? "true" : "false", u->datapath_error, u->datapath_cleanup_error,
				u->wifi_count, u->wifi_configured, u->wifi_error, u->wifi_cleanup_error,
				u->admin_pending ? "true" : "false", c->auth.enabled ? "true" : "false",
				u->auth.acquired ? "true" : "false", u->auth.result, u->auth.verified ? "true" : "false",
				c->auth.enabled && u->auth.phase == FTTR_AUTH_DONE && u->auth.verified && !u->auth.error ? "true" : "false",
				u->auth.error, u->auth.fallback_error, c->onu_policy.enabled ? "true" : "false",
				u->policy_authenticated ? "true" : "false", u->selection.action,
				c->onu_policy.enabled && u->selection.method ? u->selection.id + 1 : 0,
				u->selection.method, u->policy_error, u->reboot.attempts ? "true" : "false",
				u->reboot.replied && !u->reboot_error ? "true" :
				u->reboot.replied && u->reboot.result ? "false" : "null", u->reboot_error, u->upgrade.stage,
				u->upgrade.confirmed, u->upgrade.size, u->upgrade.action, u->upgrade.image, u->upgrade.accepted ? "true" : "false",
				u->upgrade.verified ? "true" : "false", u->upgrade.error);
		}
		fputc(']', file);
	}
	fputs("}\n", file);
	if (fclose(file))
		return -errno;
	return renameat(c->store, "status.json.new", c->store, "status.json") ? -errno : 0;
}

static void journal_table(FILE *file, const struct fttr_vlan_table *table)
{
	fprintf(file, "{\"capacity\":%u,\"length\":%u,\"received\":%u,\"complete\":%s,\"data\":\"",
		table->capacity, table->length, table->received, table->complete ? "true" : "false");
	hex(file, table->data, table->received);
	fputs("\"}", file);
}

static void journal_me(FILE *file, const struct fttr_me_job *me)
{
	fprintf(file, "{\"attempts\":%u,\"error\":%d,\"result\":%u,\"replied\":%s,\"no_retry\":%s,\"request\":\"",
		me->attempts, me->error, me->result, me->replied ? "true" : "false", me->no_retry ? "true" : "false");
	hex(file, me->request, sizeof(me->request));
	fputs("\",\"reply\":\"", file);
	hex(file, me->reply, sizeof(me->reply));
	fputs("\"}", file);
}

static void journal_sync(FILE *file, const struct onu *u, unsigned int id)
{
	const struct mib_sync *sync = &u->sync;

	fprintf(file, "{\"event\":\"mib_sync\",\"id\":%u,\"serial\":\"", id);
	hex(file, u->identity, 8);
	fprintf(file, "\",\"active\":%s,\"valid\":%s,\"value\":%u,\"observed\":%u,"
		"\"error\":%d,\"checked_ms\":%" PRId64 ",\"rescans\":%u,\"multicast_step\":%d,\"multicast_absent\":%s,\"exchange\":",
		sync->active ? "true" : "false", sync->valid ? "true" : "false", sync->value,
		sync->observed, sync->error, sync->checked, u->rescans, sync->mcast_step, u->multicast_absent ? "true" : "false");
	if (sync->job.request[4] == 0xff &&
	    (sync->job.request[5] == 0x69 || sync->job.request[5] == 0x6a))
		fputs("{\"redacted\":true}", file);
	else
		journal_me(file, &sync->job);
	fputs(",\"failure\":", file);
	if (sync->failure.request[4] == 0xff &&
	    (sync->failure.request[5] == 0x69 || sync->failure.request[5] == 0x6a))
		fprintf(file, "{\"error\":%d,\"attempts\":%u,\"result\":%u,\"redacted\":true}",
			sync->failure.error, sync->failure.attempts, sync->failure.result);
	else
		journal_me(file, &sync->failure);
	fputs("}\n", file);
}

static void journal_objects(FILE *file, const struct fttr_objects_job *job)
{
	int i;

	for (i = 0; i < job->count; i++) {
		const struct fttr_omci_object *obj = &job->objects[i];

		fprintf(file, "%s{\"class\":%u,\"instance\":%u,\"dependencies\":%u,"
			"\"create_mask\":%u,\"create\":\"", i ? "," : "", obj->class_id,
			obj->instance, obj->dependencies, obj->create.mask);
		hex(file, obj->create.value, obj->create.size);
		fprintf(file, "\",\"set_mask\":%u,\"set\":\"", obj->set.mask);
		hex(file, obj->set.value, obj->set.size);
		fputs("\"}", file);
	}
}

static int save_service(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	char temporary[24], destination[24];
	FILE *file;
	int fd, i;
	unsigned int n;

	filename(temporary, id, "service.new");
	filename(destination, id, "service");
	fd = openat(c->store, temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	if (fd < 0) return -errno;
	file = fdopen(fd, "w");
	if (!file) { int ret = -errno; close(fd); return ret; }
	for (n = 0; n < u->services.count; n++) {
		struct fttr_service_job *job = &u->services.job[n];
		struct fttr_service_config *cfg = &job->config;
		struct fttr_me_job *me = n == u->services.index ? fttr_services_pending(&u->services) : NULL;
		fttr_service_dump(job, file);
		fprintf(file, "{\"event\":\"service_datapath\",\"configured\":%s,\"interface\":\"%s\","
			"\"bridge\":\"%s\",\"ifindex\":%d,\"gem\":%u,\"mtu\":%u,\"error\":%d,\"cleanup_error\":%d}\n",
			u->ports[n].configured ? "true" : "false", u->ports[n].name,
			u->ports[n].bridge, u->ports[n].index, u->ports[n].gem,
			u->ports[n].mtu, u->datapath_error, u->datapath_cleanup_error);
		if (job->transaction.count)
			fttr_objects_job_dump(&job->transaction, file, "service_objects");
		fprintf(file, "{\"event\":\"service_recipe\",\"kind\":%u,\"uni\":%u,\"uni_ordinal\":%u,\"slot\":%u,"
			"\"factory\":%s,\"priority\":%u,\"tagged\":%s,\"filter_only\":%s,\"vid\":%u,\"multicast\":%u,\"bandwidth\":[%u,%u,%u],\"objects\":[",
			cfg->shared ? 3 : cfg->veip ? 2 : 0, cfg->uni, u->selected[n] ? u->selected[n]->ordinal : 0, cfg->slot, cfg->factory ? "true" : "false", cfg->priority, cfg->tagged ? "true" : "false",
			cfg->filter_only ? "true" : "false", cfg->vid, cfg->multicast, cfg->fixed, cfg->assured, cfg->maximum);
		journal_objects(file, &job->transaction);
		fputs("]}\n", file);
		if (cfg->multicast) {
			fprintf(file, "{\"event\":\"service_multicast_recipe\",\"port\":%u,\"uni_port\":%u,\"objects\":[",
				job->mcast_port, job->uni_count ? job->unis[job->mcast_uni].port : 0);
			journal_objects(file, &job->mcast.transaction);
			fputs("],\"table\":", file);
			journal_me(file, &job->mcast.table);
			fputs(",\"failure\":", file);
			journal_me(file, &job->mcast.failure);
			fputs("}\n", file);
		}
		if (me) {
			fputs("{\"event\":\"service_pending\",\"exchange\":", file);
			journal_me(file, me);
			fputs("}\n", file);
		}
		for (i = 0; i < (int)job->uni_count; i++) {
			struct fttr_service_uni *uni = &job->unis[i];
			struct fttr_vlan_change *vlan = &uni->vlan;

			fprintf(file, "{\"event\":\"service_uni\",\"kind\":%u,\"instance\":%u,\"port\":%u,\"objects\":[",
				uni->veip ? 2 : 0, uni->instance, uni->port);
			journal_objects(file, &uni->transaction);
			fputs("]}\n", file);
			fttr_objects_job_dump(&uni->transaction, file, "service_uni_objects");
			if (!vlan->before.me.request[2])
				continue;
			fprintf(file, "{\"event\":\"service_vlan\",\"instance\":%u,\"sent\":%u,\"verified\":%s,"
				"\"restored\":%s,\"before\":", uni->port, vlan->sent,
				vlan->verified ? "true" : "false", vlan->restored ? "true" : "false");
			journal_table(file, &vlan->before.table);
			fputs(",\"after\":", file);
			journal_table(file, &vlan->after.table);
			fputs(",\"failure\":", file);
			journal_me(file, &vlan->failure);
			fputs(",\"cleanup_failure\":", file);
			journal_me(file, &vlan->cleanup_failure);
			fputs("}\n", file);
		}
	}
	fprintf(file, "{\"event\":\"service_set\",\"count\":%u,\"current\":%u,\"configured\":%s,\"error\":%d,\"cleanup_error\":%d}\n",
		u->services.count, u->services.index, u->services.configured ? "true" : "false",
		u->services.error, u->services.cleanup_error);
	if (u->sync.enabled) journal_sync(file, u, id);
	if (fclose(file)) return -errno;
	return renameat(c->store, temporary, c->store, destination) ? -errno : 0;
}

static int save_sync(struct controller *c, unsigned int id)
{
	char temporary[24], destination[24];
	FILE *file;
	int fd;

	if (c->onu[id].service_started)
		return save_service(c, id);
	filename(temporary, id, "sync.new");
	filename(destination, id, "sync");
	fd = openat(c->store, temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	if (fd < 0)
		return -errno;
	file = fdopen(fd, "w");
	if (!file) { int ret = -errno; close(fd); return ret; }
	journal_sync(file, &c->onu[id], id);
	if (fclose(file))
		return -errno;
	return renameat(c->store, temporary, c->store, destination) ? -errno : 0;
}

static int archive_state(struct controller *c, unsigned int id, const char *suffix)
{
	char source[24], destination[24];
	char previous[16];

	snprintf(previous, sizeof(previous), "%s-old", suffix);
	filename(source, id, suffix);
	filename(destination, id, previous);
	return renameat(c->store, source, c->store, destination) && errno != ENOENT ? -errno : 0;
}

static int service_transport_failed(struct controller *c, unsigned int id, int error)
{
	int ret;

	fttr_services_cancel(&c->onu[id].services, error);
	c->onu[id].phase = ONU_SERVICE_FAILED;
	ret = save_service(c, id);
	return ret ? ret : error;
}

static int start_sync(struct controller *c, unsigned int id);
static int start_service(struct controller *c, unsigned int id);
static int withdraw_service(struct controller *c, unsigned int id, bool accel_paused);

static int save_wifi(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct wifi_run *run;
	char temporary[24], destination[24];
	FILE *file;
	int fd;

	filename(temporary, id, "wifi.new");
	filename(destination, id, "wifi");
	fd = openat(c->store, temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	if (fd < 0)
		return -errno;
	file = fdopen(fd, "w");
	if (!file) { int ret = -errno; close(fd); return ret; }

	fprintf(file, "{\"event\":\"wifi_policy\",\"id\":%u,\"serial\":\"", id);
	hex(file, u->identity, 8);
	fprintf(file, "\",\"instances\":%u,\"configured\":%u,\"error\":%d,\"cleanup_error\":%d}\n",
		u->wifi_count, u->wifi_configured, u->wifi_error, u->wifi_cleanup_error);
	for (run = u->wifi; run; run = run->next)
		fttr_wifi_job_dump(&run->job, file);
	if (fclose(file))
		return -errno;
	return renameat(c->store, temporary, c->store, destination) ? -errno : 0;
}

static void cancel_wifi(struct onu *u, int error)
{
	struct wifi_run *run;

	/* Preserve the active instance's primary/cleanup failure before marking
	 * previously completed instances stale. */
	if (u->wifi_current) {
		fttr_wifi_job_cancel(&u->wifi_current->job, error);
		if (!u->wifi_error)
			u->wifi_error = u->wifi_current->job.transaction.error;
		if (!u->wifi_cleanup_error)
			u->wifi_cleanup_error = u->wifi_current->job.transaction.cleanup_error;
	}
	for (run = u->wifi; run; run = run->next) {
		fttr_wifi_job_cancel(&run->job, error);
		if (!u->wifi_error)
			u->wifi_error = run->job.transaction.error;
		if (!u->wifi_cleanup_error)
			u->wifi_cleanup_error = run->job.transaction.cleanup_error;
	}
	u->wifi_configured = 0;
	u->wifi_current = NULL;
}

static int wifi_transport_failed(struct controller *c, unsigned int id, int error)
{
	int ret;

	cancel_wifi(&c->onu[id], error);
	ret = save_wifi(c, id);
	return ret ? ret : error;
}

static int wifi_drive(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct fttr_onu_state expected = u->hardware;
	int ret;

	memcpy(expected.serial, u->identity, 8);
	while (u->wifi_current) {
		struct fttr_objects_job *job = &u->wifi_current->job.transaction;
		int action;

		ret = fttr_miniolt_same(c->directory, id, &expected);
		if (ret) {
			cancel_wifi(u, ret);
			if (ret != -ESTALE)
				return wifi_transport_failed(c, id, ret);
			u->phase = ONU_INVENTORY;
			return save_wifi(c, id);
		}
		/* Unsent initial Get already exists; update() waits for its reply. */
		action = !job->me.attempts && job->phase == FTTR_OBJECT_CHECK ?
			FTTR_OBJECT_REQUEST : fttr_objects_job_update(job, &c->transaction);
		if (action == FTTR_OBJECT_REQUEST) {
			ret = fttr_me_job_send(&job->me, c->socket, &c->link, now_ms());
			if (ret)
				return wifi_transport_failed(c, id, ret);
			/* Track a sent Set before cancellation/replacement can occur. */
			fttr_objects_job_update(job, &c->transaction);
			return save_wifi(c, id);
		}
		if (!action)
			return save_wifi(c, id);
		if (action < 0) {
			if (!u->wifi_error)
				u->wifi_error = job->error;
			if (!u->wifi_cleanup_error)
				u->wifi_cleanup_error = job->cleanup_error;
		} else {
			u->wifi_configured++;
		}
		u->wifi_current = u->wifi_current->next;
	}
	ret = save_wifi(c, id);
	if (ret)
		return ret;
	u->phase = ONU_INVENTORY;
	ret = start_service(c, id);
	if (!ret && u->phase != ONU_SERVICE && !u->sync.enabled) {
		u->sync.enabled = true;
		ret = start_sync(c, id);
	}
	return ret;
}

static bool wifi_policy_matches(const struct controller *c, const struct wifi_policy *policy,
			       const uint8_t serial[8])
{
	const struct wifi_policy *other;

	if (!policy->wildcard)
		return !memcmp(policy->serial, serial, 8);
	for (other = c->wifi_policies; other; other = other->next)
		if (!other->wildcard && !memcmp(other->serial, serial, 8) &&
		    other->input.config.instance == policy->input.config.instance)
			return false;
	return true;
}

static int start_wifi(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct wifi_run **tail = &u->wifi;
	const struct wifi_policy *policy;

	if (!authenticated(c, u))
		return -EACCES;
	for (policy = c->wifi_policies; policy; policy = policy->next) {
		if (!wifi_policy_matches(c, policy, u->identity))
			continue;
		*tail = calloc(1, sizeof(**tail));
		if (!*tail)
			return -ENOMEM;
		fttr_wifi_job_start(&(*tail)->job, id, &c->transaction, &u->model,
			&policy->input.config, u->identity);
		u->wifi_count++;
		tail = &(*tail)->next;
	}
	if (!u->wifi) {
		int ret = start_service(c, id);

		if (!ret && c->multicast_enabled && u->phase != ONU_SERVICE && !u->sync.enabled) {
			u->sync.enabled = true;
			ret = start_sync(c, id);
		}
		return ret;
	}
	u->wifi_current = u->wifi;
	u->phase = ONU_WIFI;
	return wifi_drive(c, id);
}

static int service_drive(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct fttr_onu_state expected = u->hardware;
	struct fttr_me_job *me;
	int ret, action;

	memcpy(expected.serial, u->identity, 8);
	for (;;) {
		ret = fttr_miniolt_same(c->directory, id, &expected);
		if (ret) {
			if (ret != -ESTALE)
				return service_transport_failed(c, id, ret);
			fttr_services_cancel(&u->services, ret);
			u->phase = ONU_SERVICE_FAILED;
			break;
		}
		action = fttr_services_update(&u->services, &c->transaction, c->directory, &expected);
		if (action != 1) {
			if (action < 0) {
				bool granted = false;
				u->phase = ONU_SERVICE_FAILED;
				/* Even a failed remote rollback must stop every local grant.
				 * A read-only failed preflight owns no prior grant to clear. */
				for (unsigned int n = 0; n < u->services.count; n++)
					granted |= u->services.job[n].grant_sent || u->services.job[n].grant_verified;
				if (granted) {
					ret = withdraw_service(c, id, false);
					if (ret) return ret;
				}
			} else if (action == 2) {
				ret = fttr_miniolt_same(c->directory, id, &expected);
				if (ret && ret != -ESTALE)
					return service_transport_failed(c, id, ret);
				if (ret)
					fttr_services_cancel(&u->services, ret);
				u->phase = ret ? ONU_SERVICE_FAILED : ONU_SYNC;
				if (!ret) {
					u->service_ready = true;
					u->services.configured = false;
					for (unsigned int n = 0; n < u->services.count; n++) u->services.job[n].configured = false;
					u->sync.enabled = true;
					return start_sync(c, id);
				}
			}
			break;
		}
		ret = save_service(c, id);
		if (ret)
			return ret;
		me = fttr_services_pending(&u->services);
		ret = fttr_me_job_send(me, c->socket, &c->link, now_ms());
		if (ret)
			return service_transport_failed(c, id, ret);
	}
	ret = save_service(c, id);
	if (!ret && u->wifi && u->phase == ONU_SERVICE_FAILED && !u->sync.enabled) {
		u->sync.enabled = true;
		ret = start_sync(c, id);
	}
	return ret;
}

static const struct service_policy *matching_policy(const struct controller *c, const uint8_t serial[8])
{
	const struct service_policy *policy, *match = NULL;

	for (policy = c->policies; policy; policy = policy->next) {
		if (!policy->wildcard && !memcmp(policy->serial, serial, 8)) {
			match = policy;
			break;
		}
		if (policy->wildcard)
			match = policy;
	}
	return match;
}

static int multicast_reconcile(struct controller *c)
{
	const char *bridge = NULL;
	unsigned int id, members = 0;
	int ret = 0;

	if (!c->multicast_enabled)
		return 0;
	for (id = 0; id < FTTR_ONUS; id++) {
		struct onu *u = &c->onu[id];
		const struct service_policy *policy;
		const struct fttr_mib_entity *entity;

		if (!(u->hardware.downstream & (1U << 18)))
			continue;
		/* A blocked or unidentified registered ONU may still hold the
		 * shared GEM. Its unicast grant is not multicast isolation.
		 */
		if ((u->hardware.downstream & (1U << 16)) || !authenticated(c, u) ||
		    memcmp(u->hardware.serial, u->identity, 8) || !u->sync.valid || u->sync.recover) {
			ret = -EAGAIN;
			break;
		}
		policy = NULL;
		for (unsigned int n = 0; n < u->services.count; n++)
			if (u->selected[n] && u->selected[n]->config.multicast) policy = u->selected[n];
		if (policy) {
			if (!*policy->bridge || u->phase != ONU_CONFIGURED || !datapath_configured(u) ||
			    !u->services.configured || !multicast_service(u)->mcast.configured) {
				ret = -EAGAIN;
				break;
			}
			if (bridge && strcmp(bridge, policy->bridge)) {
				ret = -EXDEV;
				break;
			}
			bridge = policy->bridge;
			members++;
			continue;
		}
		/* Exclude both ends of the shared GEM before an unrelated ONU
		 * can coexist with this multicast domain.
		 */
		if (!u->multicast_absent) { ret = -EAGAIN; break; }
		/* A foreign CTP may use a different ME instance for Port-ID4095.
		 * The two reserved-instance probes cannot establish its absence.
		 */
		for (entity = u->model.entities; entity; entity = entity->next) {
			const uint8_t *value;
			size_t size;

			if (entity->class_id != 268 || entity->instance == 4095)
				continue;
			value = fttr_mib_entity_attr(entity, 0x8000, &size);
			if (!value || size != 2 || (value[0] == 15 && value[1] == 255)) {
				ret = -EEXIST;
				break;
			}
		}
		if (ret) break;
	}
	c->multicast_members = ret ? 0 : members;
	c->multicast_error = ret;
	if (ret || !members || c->move.active)
		return multicast_close(c);
	if (!c->multicast.index) {
		struct fttr_onu_state current[FTTR_ONUS];

		ret = fttr_miniolt_table(c->directory, current);
		if (ret)
			return ret;
		for (id = 0; id < FTTR_ONUS; id++)
			if (((current[id].downstream ^ c->onu[id].hardware.downstream) & ((1U << 18) | (1U << 16))) ||
			    memcmp(current[id].serial, c->onu[id].hardware.serial, 8)) {
				c->multicast_error = -EAGAIN;
				c->multicast_members = 0;
				return 0;
			}
	}
	if (c->multicast.index && c->multicast_checked == c->observed_at)
		return 0;
	ret = c->multicast.index ? fttr_net_mcast_verify(&c->net, &c->multicast) :
		fttr_net_mcast_create(&c->net, &c->multicast, bridge);
	c->multicast_error = ret;
	if (!ret)
		c->multicast_checked = c->observed_at;
	return ret;
}

static int multicast_update(struct controller *c)
{
	bool configured = c->multicast.configured;
	unsigned int members = c->multicast_members;
	int error = c->multicast_error;
	int ret = multicast_reconcile(c);

	if (!ret && c->observed && (configured != c->multicast.configured ||
	    members != c->multicast_members || error != c->multicast_error))
		ret = publish(c, "running", 0);
	return ret;
}

static int start_service(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	const struct service_policy *first = matching_policy(c, u->identity), *policy;
	struct fttr_service_config configs[FTTR_SERVICES];
	unsigned int slot, count = 0;
	bool missing = false;
	int ret;

	if (!authenticated(c, u)) return -EACCES;
	if (!first) return 0;
	for (slot = 1; slot <= FTTR_SERVICES; slot++)
		for (policy = c->policies; policy; policy = policy->next) {
			const struct fttr_mib_entity *entity;
			unsigned int ordinal = 0;
			if (policy->config.slot != slot || policy->wildcard != first->wildcard ||
			    (!policy->wildcard && memcmp(policy->serial, u->identity, 8))) continue;
			configs[count] = policy->config;
			configs[count].backend = c->backend;
			for (entity = u->model.entities; entity; entity = entity->next)
				if (entity->class_id == (policy->config.veip ? 329 : 11) && ordinal++ == policy->ordinal) break;
			missing |= !entity && !configs[count].shared;
			configs[count].uni = entity && !configs[count].shared ? entity->instance : 0;
			u->selected[count++] = policy;
		}
	ret = archive_state(c, id, "service");
	if (ret) return ret;
	u->service_started = true;
	if (missing) {
		u->services.count = count;
		u->services.error = -ENOENT;
		u->services.stage = FTTR_SERVICES_FAILED;
		for (unsigned int n = 0; n < count; n++) {
			struct fttr_service_job *job = &u->services.job[n];
			job->config = configs[n];
			job->phase = "select-uni";
			job->error = -ENOENT;
			job->stage = FTTR_SERVICE_FAILED;
			job->transaction.me.onu = id;
			memcpy(job->transaction.serial, u->identity, 8);
		}
		u->phase = ONU_SERVICE_FAILED;
		return save_service(c, id);
	}
	ret = fttr_services_start(&u->services, id, &c->transaction, &u->model, configs, count, u->identity);
	if (ret) { u->phase = ONU_SERVICE_FAILED; return save_service(c, id); }
	u->phase = ONU_SERVICE;
	return service_drive(c, id);
}

static int withdraw_service(struct controller *c, unsigned int id, bool accel_paused)
{
	struct onu *u = &c->onu[id];
	unsigned int n;
	bool paused = false;
	int ret, cleanup = multicast_close(c);

	c->multicast_members = 0;
	for (n = 0; n < u->services.count; n++) {
		paused |= u->ports[n].index != 0 && !accel_paused;
		u->ports[n].configured = false;
	}
	if (!cleanup && paused) cleanup = forwarding(c, false);
	if (!cleanup)
		for (n = u->services.count; n; n--) {
			ret = fttr_net_remove(&c->net, &u->ports[n - 1]);
			if (ret && !cleanup) cleanup = ret;
		}
	if (cleanup) u->datapath_cleanup_error = cleanup;
	for (n = u->services.count; n; n--) {
		struct fttr_service_job *job = &u->services.job[n - 1];
		if (!job->config.slot || job->grant_withdrawn) continue;
		ret = fttr_miniolt_tcont(c->directory, id, job->config.slot, 0, 0, 0);
		if (!ret) ret = fttr_miniolt_tcont_read(c->directory, id, job->config.slot, &job->observed);
		if (!ret && (job->observed.fixed || job->observed.limits)) ret = -EUCLEAN;
		if (ret) {
			job->cleanup_error = u->services.cleanup_error = ret;
			if (!cleanup) cleanup = ret;
		} else job->grant_withdrawn = true;
	}
	if (!cleanup && paused) cleanup = forwarding(c, true);
	if (cleanup) { u->datapath_cleanup_error = cleanup; save_service(c, id); }
	return cleanup;
}

static int cancel_state(struct controller *c, unsigned int id, bool accel_paused, bool keep_management)
{
	struct onu *u = &c->onu[id];
	int ret;

	fttr_auth_cancel(&u->auth, -ESTALE);
	u->policy_authenticated = u->policy_move = false;
	u->auth_retry = 0;
	u->policy_error = 0;
	memset(&u->selection, 0, sizeof(u->selection));
	u->selection.action = FTTR_ONU_REJECT;
	if (u->wifi) {
		cancel_wifi(u, -ESTALE);
		ret = save_wifi(c, id);
		if (!ret)
			ret = archive_state(c, id, "wifi");
		if (ret)
			return ret;
		while (u->wifi) {
			struct wifi_run *next = u->wifi->next;

			free(u->wifi);
			u->wifi = next;
		}
		u->wifi_count = 0;
		u->wifi_error = u->wifi_cleanup_error = 0;
	}
	u->service_ready = u->multicast_absent = false;
	if (u->dump) {
		ret = fclose(u->dump);
		u->dump = NULL;
		if (ret)
			return -errno;
	}
	if (u->service_started) {
		if (u->sync.active) {
			u->sync.job.state = FTTR_ME_FAILED;
			u->sync.job.error = -ESTALE;
			if (!u->sync.error)
				u->sync.error = -ESTALE;
		}
		u->sync.active = false;
		u->sync.valid = false;
		if (u->services.stage != FTTR_SERVICES_RELEASED)
			fttr_services_cancel(&u->services, -ESTALE);
		ret = withdraw_service(c, id, accel_paused);
		if (ret)
			return ret;
		ret = save_service(c, id);
		if (ret)
			return ret;
		ret = archive_state(c, id, "service");
		if (ret)
			return ret;
		fttr_services_clear(&u->services);
		u->service_started = false;
	}
	memset(u->ports, 0, sizeof(u->ports));
	memset(u->selected, 0, sizeof(u->selected));
	u->datapath_error = u->datapath_cleanup_error = 0;
	ret = remove_file(c->store, id, "pending");
	if (ret)
		return ret;
	ret = remove_file(c->store, id, "mib");
	if (ret)
		return ret;
	if (u->grant && !keep_management) {
		ret = fttr_miniolt_management(c->directory, id, false);
		if (ret)
			return ret;
		u->grant = false;
	}
	memset(&u->job, 0, sizeof(u->job));
	if (u->sync.enabled) {
		ret = save_sync(c, id);
		if (ret)
			return ret;
	}
	memset(&u->sync, 0, sizeof(u->sync));
	ret = archive_state(c, id, "sync");
	if (ret)
		return ret;
	fttr_mib_model_clear(&u->model);
	memset(&u->auth, 0, sizeof(u->auth));
	return 0;
}

static int cancel(struct controller *c, unsigned int id, bool accel_paused)
{
	return cancel_state(c, id, accel_paused, false);
}

static int failed(struct controller *c, unsigned int id, int error)
{
	struct onu *u = &c->onu[id];
	char pending[24], destination[24];
	const struct wifi_policy *policy;
	bool watch = u->dump && matching_policy(c, u->identity);

	for (policy = c->wifi_policies; u->dump && policy; policy = policy->next)
		watch |= wifi_policy_matches(c, policy, u->identity);

	u->phase = ONU_FAILED;
	u->job.step = FTTR_MIB_FAILED;
	u->job.error = error;
	fttr_mib_model_clear(&u->model);
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
	if (watch) {
		u->sync.enabled = true;
		u->sync.recover = true;
		u->sync.error = error;
		u->sync.due = now_ms() + 2000;
		return save_sync(c, id);
	}
	return 0;
}

static int clear_auth_grants(struct controller *c)
{
	struct fttr_tcont_grant grant;
	unsigned int id, slot, last = c->backend == FTTR_FPGA1 ? 1 : 7;
	int ret;

	/* Exclusive controller ownership and FE/VLAN withdrawal precede this.
	 * An earlier process can leave hardware grants behind even when no
	 * current service policy names them. No data grant is trusted before
	 * the new controller authenticates that registration. Keep ASIC slot0
	 * for management; FPGA1 service slot1 maps to its sole physical slot0.
	 */
	for (id = 0; id < FTTR_ONUS; id++)
		for (slot = 1; slot <= last; slot++) {
			if (stopping)
				return -EINTR;
			ret = fttr_miniolt_tcont_read(c->directory, id, slot, &grant);
			if (ret)
				return ret;
			if (!grant.fixed && !grant.limits)
				continue;
			ret = fttr_miniolt_tcont(c->directory, id, slot, 0, 0, 0);
			if (!ret)
				ret = fttr_miniolt_tcont_read(c->directory, id, slot, &grant);
			if (ret)
				return ret;
			if (grant.fixed || grant.limits)
				return -EUCLEAN;
		}
	return 0;
}

static int start_mib(struct controller *c, unsigned int id)
{
	static const uint8_t empty[8];
	struct onu *u = &c->onu[id];
	char name[24];
	int fd, ret;

	memset(&u->upgrade, 0, sizeof(u->upgrade));
	memset(&u->reboot, 0, sizeof(u->reboot));
	u->reboot_error = 0;
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
	ret = c->onu_policy.enabled ? fttr_auth_credentials(&u->auth,
		c->auth.enabled || fttr_onu_policy_needs_loid(&c->onu_policy), id, &c->transaction) :
		fttr_auth_start(&u->auth, &c->auth, id, &c->transaction);
	if (ret)
		return ret;
	if (c->auth.enabled || c->onu_policy.enabled) {
		u->phase = ONU_AUTH;
		if (u->auth.phase == FTTR_AUTH_READY)
			return auth_drive(c, id);
		return fttr_me_job_send(&u->auth.me, c->socket, &c->link, now_ms());
	}
	fttr_mib_job_start(&u->job, id, &c->transaction);
	return fttr_mib_job_send(&u->job, c->socket, &c->link, now_ms());
}

static int auth_failed(struct controller *c, unsigned int id, int error)
{
	struct onu *u = &c->onu[id];
	char pending[24], destination[24];
	int ret;

	u->policy_authenticated = u->policy_move = false;
	if (u->dump) {
		fprintf(u->dump, "{\"event\":\"authentication\",\"global_result\":%u,\"verified\":%s,"
			"\"action\":%u,\"logical_id\":%u,\"error\":%d}\n",
			u->auth.result, u->auth.verified ? "true" : "false", u->selection.action,
			u->selection.method ? u->selection.id + 1 : 0, error);
		ret = fclose(u->dump);
		u->dump = NULL;
		if (ret)
			return -errno;
		filename(pending, id, "pending");
		filename(destination, id, "failed");
		if (renameat(c->store, pending, c->store, destination))
			return -errno;
	}
	u->job.error = error;
	u->phase = ONU_AUTH_FAILED;
	return 0;
}

static int auth_drive(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct fttr_onu_state expected = u->hardware;
	int ret;

	memcpy(expected.serial, u->identity, 8);
	ret = fttr_miniolt_same(c->directory, id, &expected);
	if (ret && ret != -ESTALE)
		return ret;
	if (ret)
		fttr_auth_cancel(&u->auth, ret);
	else {
		ret = fttr_auth_advance(&u->auth, &c->auth, &c->transaction);
		if (ret < 0)
			return ret;
		if (ret)
			return fttr_me_job_send(&u->auth.me, c->socket, &c->link, now_ms());
	}
	if (u->auth.error) {
		/* An unrelated LOID row must not make an SN-only peer depend on
		 * credential MEs. Re-run selection with explicitly missing input;
		 * only an independently proven match/move may continue.
		 */
		if (c->onu_policy.enabled && !c->auth.enabled &&
		    (u->auth.error == -EREMOTEIO || u->auth.error == -ETIMEDOUT)) {
			struct fttr_onu_credentials observed = { 0 };
			struct fttr_onu_selection selected;
			bool live[FTTR_ONUS];

			memcpy(observed.serial, u->identity, 8);
			for (unsigned int i = 0; i < FTTR_ONUS; i++)
				live[i] = c->onu[i].policy_authenticated;
			selected = fttr_onu_policy_select(&c->onu_policy, id, &observed, live);
			if (selected.action == FTTR_ONU_MATCH || selected.action == FTTR_ONU_MOVE) {
				u->auth.error = 0;
				u->auth.phase = FTTR_AUTH_READY;
				u->auth.me.onu = id;
			}
		}
		if (u->auth.error)
			return auth_failed(c, id, u->auth.error);
	}
	if (u->auth.phase == FTTR_AUTH_READY) {
		struct fttr_onu_credentials observed = { .loid_valid = u->auth.acquired };
		bool live[FTTR_ONUS];

		memcpy(observed.serial, u->identity, 8);
		memcpy(observed.loid, u->auth.loid, sizeof(observed.loid));
		memcpy(observed.key, u->auth.key, sizeof(observed.key));
		/* Only a current-process authenticated identity reserves a live row.
		 * PLOAM is deliberately invalid until its acquisition is established.
		 */
		for (unsigned int i = 0; i < FTTR_ONUS; i++)
			live[i] = c->onu[i].policy_authenticated;
		u->selection = fttr_onu_policy_select(&c->onu_policy, id, &observed, live);
		memset(&observed, 0, sizeof(observed));
		u->policy_error = u->selection.error;
		switch (u->selection.action) {
		case FTTR_ONU_BIND:
		case FTTR_ONU_LEARN:
			ret = fttr_onu_policy_bind(&c->onu_policy, &u->selection, u->identity);
			if (ret) {
				u->policy_error = ret;
				fttr_auth_cancel(&u->auth, ret);
				/* Persistence may have reached rename before a failed fsync. */
				return ret;
			}
			u->auth_retry = now_ms() + 2000;
			u->policy_error = -EAGAIN;
			fttr_auth_cancel(&u->auth, -EAGAIN);
			return auth_failed(c, id, -EAGAIN);
		case FTTR_ONU_WAIT:
		case FTTR_ONU_REJECT:
			u->policy_error = u->selection.error ? u->selection.error : -EACCES;
			fttr_auth_cancel(&u->auth, u->policy_error);
			return auth_failed(c, id, u->policy_error);
		case FTTR_ONU_MOVE:
			/* Original assignment precedes global authentication. Inventory
			 * may establish safe migration, but must not create services.
			 */
			fttr_auth_cancel(&u->auth, 0);
			u->policy_move = true;
			break;
		case FTTR_ONU_MATCH:
			ret = fttr_auth_decide(&u->auth, &c->auth, &c->transaction);
			if (ret < 0)
				return ret;
			if (ret)
				return fttr_me_job_send(&u->auth.me, c->socket, &c->link, now_ms());
			break;
		}
	}
	if (c->onu_policy.enabled && !u->policy_move)
		u->policy_authenticated = true;
	u->phase = ONU_MIB;
	fttr_mib_job_start(&u->job, id, &c->transaction);
	return fttr_mib_job_send(&u->job, c->socket, &c->link, now_ms());
}

static int sync_io_failed(struct controller *c, unsigned int id, int error)
{
	struct mib_sync *sync = &c->onu[id].sync;

	sync->active = false;
	sync->valid = false;
	sync->error = error;
	if (!sync->recover) {
		sync->failure = sync->job;
		sync->failure.state = FTTR_ME_FAILED;
		sync->failure.error = error;
	}
	if (c->onu[id].service_started)
		return service_transport_failed(c, id, error);
	{
		int ret = save_sync(c, id);

		return ret ? ret : error;
	}
}

static int sync_failed(struct controller *c, unsigned int id, int error)
{
	struct onu *u = &c->onu[id];
	int ret = multicast_close(c);

	if (ret)
		return ret;
	c->multicast_members = 0;
	u->sync.active = false;
	u->sync.valid = false;
	u->sync.error = error;
	if (!u->sync.recover) {
		u->sync.failure = u->sync.job;
		u->sync.failure.state = FTTR_ME_FAILED;
		u->sync.failure.error = error;
	}
	u->sync.recover = true;
	u->multicast_absent = false;
	u->sync.due = now_ms() + 2000;
	if (u->wifi) {
		cancel_wifi(u, error);
		ret = save_wifi(c, id);
		if (ret)
			return ret;
	}
	if (u->service_started) {
		fttr_services_cancel(&u->services, error);
		u->phase = ONU_SERVICE_FAILED;
		ret = withdraw_service(c, id, false);
		if (ret)
			return ret;
	}
	return save_sync(c, id);
}

static int sync_send(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct fttr_onu_state expected = u->hardware;
	int ret;

	memcpy(expected.serial, u->identity, 8);
	ret = fttr_miniolt_same(c->directory, id, &expected);
	if (ret)
		return ret == -ESTALE ? sync_failed(c, id, ret) : sync_io_failed(c, id, ret);
	u->sync.active = true;
	ret = save_sync(c, id);
	if (ret)
		return ret;
	ret = fttr_me_job_send(&u->sync.job, c->socket, &c->link, now_ms());
	return ret ? sync_io_failed(c, id, ret) : 0;
}

static int start_sync(struct controller *c, unsigned int id)
{
	struct mib_sync *sync = &c->onu[id].sync;

	sync->gem_probe = false;
	sync->service_probe = 0;
	sync->wifi_probe = NULL;
	sync->wifi_group = 0;
	sync->mcast_step = -1;
	fttr_me_job_start(&sync->job, id, &c->transaction, FTTR_ME_GET, 2, 0, 0x8000, NULL, 0);
	return sync_send(c, id);
}

static int rescan_sync(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct mib_sync previous = u->sync;
	int ret;

	u->sync.active = false;
	u->sync.valid = false;
	ret = cancel(c, id, false);
	if (ret)
		return ret;
	u->sync = previous;
	u->sync.wifi_probe = NULL;
	u->sync.enabled = false;
	u->sync.recover = true;
	ret = start_mib(c, id);
	if (!ret)
		u->rescans++;
	return ret;
}

static int sync_wifi_next(struct controller *c, unsigned int id)
{
	struct mib_sync *sync = &c->onu[id].sync;

	while (sync->wifi_probe) {
		struct fttr_objects_job *job = &sync->wifi_probe->job.transaction;

		if (job->phase == FTTR_OBJECT_CONFIGURED && sync->wifi_group < job->count) {
			const struct fttr_omci_object *object = &job->objects[sync->wifi_group];

			fttr_me_job_start(&sync->job, id, &c->transaction, FTTR_ME_GET,
				object->class_id, object->instance, object->create.mask, NULL, 0);
			{
				int ret = sync_send(c, id);

				return ret ? ret : 1;
			}
		}
		sync->wifi_probe = sync->wifi_probe->next;
		sync->wifi_group = 0;
	}
	/* Do not retain a credential-bearing last Get in the generic sync journal. */
	memset(&sync->job, 0, sizeof(sync->job));
	return 0;
}

/* Recheck the multicast objects and initialized IPv4 row even when ONU
 * Data sync remains zero. Remote ACL hardware success is not implied by MIB.
 */
static int sync_mcast_next(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct mib_sync *sync = &u->sync;
	bool member = u->service_ready && multicast_service(u) != NULL;
	const struct fttr_omci_values *values;
	const struct fttr_omci_object *object;
	uint8_t sequence[2] = { 0 };
	int ret;

	if (!member) {
		if (sync->mcast_step >= 2) { u->multicast_absent = sync->mcast_absent; return 0; }
		fttr_me_job_start(&sync->job, id, &c->transaction, FTTR_ME_GET,
			sync->mcast_step ? 281 : 268, 4095, sync->mcast_step ? 0xf300 : 0xfac0, NULL, 0);
	} else {
		while (sync->mcast_step < 14) {
			object = &multicast_service(u)->mcast.objects[sync->mcast_step / 2];
			values = sync->mcast_step & 1 ? &object->set : &object->create;
			if (values->mask) break;
			sync->mcast_step++;
		}
		if (sync->mcast_step >= 16) return 0;
		if (sync->mcast_step < 14) {
			object = &multicast_service(u)->mcast.objects[sync->mcast_step / 2];
			values = sync->mcast_step & 1 ? &object->set : &object->create;
			fttr_me_job_start(&sync->job, id, &c->transaction, FTTR_ME_GET,
				object->class_id, object->instance, values->mask, NULL, 0);
		} else {
			fttr_me_job_start(&sync->job, id, &c->transaction,
				sync->mcast_step == 14 ? FTTR_ME_GET : FTTR_ME_GET_NEXT,
				281, 4095, 0x80, sync->mcast_step == 15 ? sequence : NULL, sync->mcast_step == 15 ? 2 : 0);
		}
	}
	ret = sync_send(c, id);

	return ret ? ret : 1;
}

static int sync_reply(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct mib_sync *sync = &u->sync;
	struct fttr_onu_state expected = u->hardware;
	int ret;

	memcpy(expected.serial, u->identity, 8);
	ret = fttr_miniolt_same(c->directory, id, &expected);
	if (ret)
		return ret == -ESTALE ? sync_failed(c, id, ret) : sync_io_failed(c, id, ret);
	sync->active = false;
	if (sync->mcast_step >= 0) {
		bool member = u->service_ready && multicast_service(u) != NULL;
		bool mismatch = false;

		if (!member) {
			if (sync->job.error && !(sync->job.error == -EREMOTEIO && sync->job.result == 5))
				return sync_failed(c, id, sync->job.error);
			if (!sync->job.error) u->multicast_absent = sync->mcast_absent = false;
		} else {
			if (sync->job.error) return sync_failed(c, id, sync->job.error);
			if (sync->mcast_step < 14) {
				const struct fttr_omci_object *object = &multicast_service(u)->mcast.objects[sync->mcast_step / 2];
				const struct fttr_omci_values *values = sync->mcast_step & 1 ? &object->set : &object->create;

				mismatch = memcmp(sync->job.reply + 11, values->value, values->size) != 0;
			} else if (sync->mcast_step == 14) {
				const uint8_t *p = sync->job.reply + 11;
				uint32_t length = (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];

				mismatch = length != (multicast_service(u)->mcast.doubled ? 24U : 12U);
			} else {
				mismatch = memcmp(sync->job.reply + 11, multicast_service(u)->mcast.table_row, 12) != 0;
			}
			if (mismatch) return sync_failed(c, id, -EUCLEAN);
		}
		sync->mcast_step++;
		ret = sync_mcast_next(c, id);
		if (ret) return ret < 0 ? ret : 0;
	} else if (sync->wifi_probe) {
		const struct fttr_omci_values *values =
			&sync->wifi_probe->job.objects[sync->wifi_group].create;

		if ((sync->job.error == -EREMOTEIO && sync->job.result == 5) ||
		    (!sync->job.error && memcmp(sync->job.reply + 11, values->value, values->size))) {
			sync->error = -ESTALE;
			return rescan_sync(c, id);
		}
		if (sync->job.error)
			return sync_failed(c, id, sync->job.error);
		sync->wifi_group++;
		ret = sync_wifi_next(c, id);
		if (ret)
			return ret < 0 ? ret : 0;
	} else if (sync->gem_probe) {
		/* The fixed service plan's class268 CTP is object3; its complete
		 * selected attributes prove it survived when data-sync remains zero. */
		const struct fttr_omci_values *values = &u->services.job[sync->service_probe].objects[3].create;

		if ((sync->job.error == -EREMOTEIO && sync->job.result == 5) ||
		    (!sync->job.error && memcmp(sync->job.reply + 11, values->value, values->size))) {
			sync->error = -ESTALE;
			return rescan_sync(c, id);
		}
		if (sync->job.error)
			return sync_failed(c, id, sync->job.error);
		if (++sync->service_probe < u->services.count) {
			const struct fttr_service_job *job = &u->services.job[sync->service_probe];
			fttr_me_job_start(&sync->job, id, &c->transaction, FTTR_ME_GET,
				268, job->gem, job->objects[3].create.mask, NULL, 0);
			return sync_send(c, id);
		}
		sync->gem_probe = false;
		sync->wifi_probe = u->wifi;
		if (sync->wifi_probe) {
			ret = sync_wifi_next(c, id);
			if (ret)
				return ret < 0 ? ret : 0;
		}
	} else {
		if (sync->job.error)
			return sync_failed(c, id, sync->job.error);
		sync->observed = sync->job.reply[11];
		if (sync->recover)
			return rescan_sync(c, id);
		if (sync->valid && sync->observed != sync->value) {
			sync->error = -ESTALE;
			return rescan_sync(c, id);
		}
		if (!sync->observed && u->service_ready) {
			sync->gem_probe = true;
			sync->service_probe = 0;
			fttr_me_job_start(&sync->job, id, &c->transaction, FTTR_ME_GET,
				268, u->services.job[sync->service_probe].gem, u->services.job[sync->service_probe].objects[3].create.mask, NULL, 0);
			return sync_send(c, id);
		}
		sync->wifi_probe = u->wifi;
		if (sync->wifi_probe) {
			ret = sync_wifi_next(c, id);
			if (ret)
				return ret < 0 ? ret : 0;
		}
	}
	if (c->multicast_enabled && sync->mcast_step < 0) {
		sync->mcast_step = 0;
		sync->mcast_absent = true;
		ret = sync_mcast_next(c, id);
		if (ret) return ret < 0 ? ret : 0;
	}
	sync->value = sync->observed;
	sync->valid = true;
	sync->error = 0;
	sync->checked = now_ms();
	sync->due = sync->checked + 2000;
	if (!u->service_ready) {
		u->phase = u->service_started ? ONU_SERVICE_FAILED : ONU_INVENTORY;
		return save_sync(c, id);
	}
	u->services.configured = true;
	for (unsigned int n = 0; n < u->services.count; n++) {
		const struct service_policy *policy = u->selected[n];
		struct fttr_net_port *port = &u->ports[n];
		u->services.job[n].configured = true;
		if (!*policy->bridge) continue;
		/* Every GEM in this local broadcast domain must suppress the
		 * duplicate shared-GEM copy, not only the job owning remote MEs. */
		port->multicast = false;
		for (unsigned int m = 0; m < u->services.count; m++)
			if (u->selected[m]->config.multicast && !strcmp(policy->bridge, u->selected[m]->bridge))
				port->multicast = true;
		ret = port->configured ? fttr_net_verify(&c->net, port) :
			fttr_net_create(&c->net, port, id, u->identity, u->services.job[n].gem, policy->bridge);
		if (ret) {
			u->datapath_error = ret;
			u->sync.enabled = false;
			fttr_services_cancel(&u->services, ret);
			u->phase = ONU_SERVICE_FAILED;
			ret = withdraw_service(c, id, false);
			return ret ? ret : save_service(c, id);
		}
	}
	u->phase = ONU_CONFIGURED;
	return save_service(c, id);
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
	/* Admission sees the complete registration snapshot before cancellation
	 * clears old ownership or starts a replacement.
	 */
	for (id = 0; id < FTTR_ONUS; id++)
		c->onu[id].hardware = table[id];
	ret = multicast_update(c);
	if (ret)
		return ret;
	/* Retire the complete snapshot before selecting any new identity. A
	 * lower hardware slot may otherwise skip a departed higher live row
	 * and auto-learn the same serial into the wrong logical reservation.
	 */
	for (id = 0; id < FTTR_ONUS; id++) {
		struct onu *u = &c->onu[id];

		u->hardware = table[id];
		if (!u->policy_authenticated ||
		    (c->move.active && (id == c->move.request.id || id == c->move.request.target)))
			continue;
		if (!(table[id].downstream & (1U << 18)) || (table[id].downstream & (1U << 16)) ||
		    memcmp(u->identity, table[id].serial, 8)) {
			ret = cancel(c, id, false);
			if (ret)
				return ret;
			u->phase = ONU_ABSENT;
		}
	}
	for (id = 0; id < FTTR_ONUS; id++) {
		struct onu *u = &c->onu[id];
		enum onu_phase inactive = table[id].downstream & (1U << 16) ? ONU_BLOCKED : ONU_ABSENT;

		u->hardware = table[id];
		if (c->upgrade.active && id == c->upgrade.request.id) {
			if ((table[id].downstream & (1U << 18)) && inactive != ONU_BLOCKED &&
			    !memcmp(c->upgrade.request.serial, table[id].serial, 8)) continue;
			ret = upgrade_finish(c, -ESTALE);
			if (ret) return ret;
		}
		if (c->reboot.active && id == c->reboot.request.id) {
			if ((table[id].downstream & (1U << 18)) && inactive != ONU_BLOCKED &&
			    !memcmp(c->reboot.request.serial, table[id].serial, 8))
				continue;
			ret = reboot_finish(c, -ESTALE);
			if (ret) return ret;
		}
		if (c->move.active && (id == c->move.request.id || id == c->move.request.target))
			continue;
		if (u->admin_pending) {
			if (memcmp(u->admin_serial, table[id].serial, 8))
				u->admin_pending = false;
			else
				continue;
		}
		if (!(table[id].downstream & (1U << 18)) || inactive == ONU_BLOCKED) {
			if (u->phase != inactive) {
				ret = cancel(c, id, false);
				if (ret)
					return ret;
			}
			u->phase = inactive;
			continue;
		}
		if (u->phase == ONU_ABSENT || u->phase == ONU_BLOCKED || memcmp(u->identity, table[id].serial, 8)) {
			ret = cancel(c, id, false);
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
	if (!authenticated(c, u) && !u->policy_move)
		return -EACCES;
	ret = fttr_mib_model_dump(&u->model, u->dump);
	if (ret)
		return ret;
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
	memset(&u->sync, 0, sizeof(u->sync));
	return u->policy_move ? policy_move(c, id) : start_wifi(c, id);
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
			if (u->phase == ONU_UPGRADE_WAIT) return 0;
			if (u->phase == ONU_UPGRADE &&
			    ((uint16_t)message.body[0] << 8 | message.body[1]) == u->upgrade.me.transaction)
				return upgrade_finish(c, error);
			if (u->phase == ONU_REBOOT_WAIT) return 0;
			if (u->phase == ONU_REBOOT &&
			    ((uint16_t)message.body[0] << 8 | message.body[1]) == u->reboot.transaction)
				return reboot_finish(c, error);
			if (u->phase == ONU_MOVE) {
				struct fttr_me_job *me = fttr_services_pending(&u->services);

				if (me && ((uint16_t)message.body[0] << 8 | message.body[1]) == me->transaction) {
					me->state = FTTR_ME_FAILED;
					me->error = error;
					return move_drive(c);
				}
			}
			if (u->phase == ONU_AUTH &&
			    ((uint16_t)message.body[0] << 8 | message.body[1]) == u->auth.me.transaction) {
				u->auth.me.state = FTTR_ME_FAILED;
				u->auth.me.error = error;
				ret = auth_drive(c, message.onu);
				return ret ? ret : publish(c, "running", 0);
			}
			if (u->sync.active &&
			    ((uint16_t)message.body[0] << 8 | message.body[1]) == u->sync.job.transaction) {
				u->sync.job.state = FTTR_ME_FAILED;
				u->sync.job.error = error;
				ret = sync_reply(c, message.onu);
				return ret ? ret : publish(c, "running", 0);
			}
			if (u->phase == ONU_SERVICE) {
				struct fttr_me_job *me = fttr_services_pending(&u->services);

				if (((uint16_t)message.body[0] << 8 | message.body[1]) == me->transaction) {
					me->state = FTTR_ME_FAILED;
					me->error = error;
					ret = service_drive(c, message.onu);
					if (ret)
						return ret;
				}
			}
			if (u->phase == ONU_WIFI && u->wifi_current) {
				struct fttr_me_job *me = &u->wifi_current->job.transaction.me;

				if (((uint16_t)message.body[0] << 8 | message.body[1]) == me->transaction) {
					me->state = FTTR_ME_FAILED;
					me->error = error;
					ret = wifi_drive(c, message.onu);
					return ret ? ret : publish(c, "running", 0);
				}
			}
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
	if (u->phase == ONU_UPGRADE_WAIT) return 0;
	if (u->phase == ONU_UPGRADE) {
		if (!fttr_upgrade_reply(&u->upgrade, &c->transaction, &message, now_ms())) return 0;
		return upgrade_drive(c);
	}
	if (u->phase == ONU_REBOOT_WAIT) return 0;
	if (u->phase == ONU_REBOOT) {
		if (!fttr_me_job_reply(&u->reboot, &message)) return 0;
		return reboot_drive(c);
	}
	if (u->phase == ONU_MOVE) {
		struct fttr_me_job *me = fttr_services_pending(&u->services);

		if (!me || !fttr_me_job_reply(me, &message))
			return 0;
		return move_drive(c);
	}
	if (u->phase == ONU_AUTH) {
		if (!fttr_me_job_reply(&u->auth.me, &message))
			return 0;
		ret = auth_drive(c, message.onu);
		return ret ? ret : publish(c, "running", 0);
	}
	if (u->phase == ONU_WIFI && u->wifi_current) {
		if (!fttr_me_job_reply(&u->wifi_current->job.transaction.me, &message))
			return 0;
		ret = wifi_drive(c, message.onu);
		return ret ? ret : publish(c, "running", 0);
	}
	if (u->sync.active) {
		if (!fttr_me_job_reply(&u->sync.job, &message))
			return 0;
		ret = sync_reply(c, message.onu);
		return ret ? ret : publish(c, "running", 0);
	}
	if (u->phase == ONU_SERVICE) {
		if (!fttr_me_job_reply(fttr_services_pending(&u->services), &message))
			return 0;
		ret = service_drive(c, message.onu);
		return ret ? ret : publish(c, "running", 0);
	}
	sequence = u->job.sequence;
	ret = fttr_mib_job_reply(&u->job, &message, &c->transaction, &record);
	if (ret < 0) {
		fprintf(u->dump, "{\"event\":\"mib_reply_error\",\"error\":%d,\"body\":\"", ret);
		/* Preserve the transaction/class/instance header only: malformed
		 * payloads cannot safely identify the private credential fields.
		 */
		if (!c->wifi_policies)
			hex(u->dump, message.body, message.length < 8 ? message.length : 8);
		fputs("\"}\n", u->dump);
		ret = failed(c, message.onu, ret);
		return ret ? ret : publish(c, "running", 0);
	}
	if (!ret)
		return 0;
	if (ret & FTTR_MIB_RECORD) {
		int result;

		fprintf(u->dump, "{\"event\":\"mib_record\",\"sequence\":%u,\"class\":%u,"
			"\"instance\":%u,\"mask\":%u,\"attributes\":\"", sequence, record.class_id, record.instance, record.mask);
		if (!fttr_mib_private(record.class_id, record.mask))
			hex(u->dump, record.attributes, sizeof(record.attributes));
		fputs("\"}\n", u->dump);
		if (ferror(u->dump))
			return -EIO;
		result = fttr_mib_model_add(&u->model, &record);
		if (result == -EMSGSIZE) {
			result = failed(c, message.onu, result);
			return result ? result : publish(c, "running", 0);
		}
		if (result < 0)
			return result;
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

static int open_control(struct controller *c)
{
	struct sockaddr_un address = { .sun_family = AF_UNIX };
	struct stat st;
	mode_t mask;
	int enabled = 1, ret;

	if (fstat(c->store, &st))
		return -errno;
	if (st.st_uid != geteuid() || (st.st_mode & 0022))
		return -EACCES;
	/* Binding through the directory descriptor also supports long paths. */
	snprintf(address.sun_path, sizeof(address.sun_path), "/proc/self/fd/%d/control.sock", c->store);
	if (unlinkat(c->store, "control.sock", 0) && errno != ENOENT)
		return -errno;
	c->control = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
	if (c->control < 0)
		return -errno;
	if (setsockopt(c->control, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)))
		return -errno;
	mask = umask(0077);
	ret = bind(c->control, (struct sockaddr *)&address, sizeof(address)) ? -errno : 0;
	umask(mask);
	return ret;
}

static int admin_request(struct controller *c, const struct fttr_admin_request *request,
			 bool recovery, int *fatal)
{
	struct fttr_onu_state observed;
	struct onu *u;
	int ret;

	*fatal = 0;
	if (request->version != 1 || request->id >= FTTR_ONUS || request->action > FTTR_ADMIN_DEACTIVATE)
		return -EINVAL;
	if (c->upgrade.active && request->id == c->upgrade.request.id) return -EBUSY;
	if (c->reboot.active && request->id == c->reboot.request.id)
		return -EBUSY;
	if (c->move.active && (request->id == c->move.request.id || request->id == c->move.request.target))
		return -EBUSY;
	u = &c->onu[request->id];
	ret = fttr_miniolt_one(c->directory, request->id, &observed);
	if (ret)
		return ret;
	if (memcmp(observed.serial, request->serial, 8))
		return -ESTALE;
	if (recovery && ((observed.downstream & (1U << 16)) ||
	    !fttr_miniolt_recovery_needed(c->backend, observed.upstream)))
		return -EAGAIN;
	/* A later permanent block/resume replaces this registration's timer. */
	u->admin_pending = false;
	/* Keep FE admission closed through port removal, grant withdrawal and
	 * the hardware state change. cancel() must not reopen it midway.
	 */
	ret = forwarding(c, false);
	if (!ret)
		ret = cancel(c, request->id, true);
	if (ret) {
		*fatal = ret;
		return ret;
	}
	u->phase = ONU_ABSENT;
	/* The driver rechecks the serial under the same lock as state/DBA IO. */
	ret = fttr_miniolt_admin(c->directory, request->id,
		recovery ? 3 : request->action != FTTR_ADMIN_RESUME, request->serial);
	if (ret && ret != -ESTALE && !(recovery && ret == -EAGAIN)) {
		*fatal = ret;
		return ret;
	}
	if (!ret) {
		ret = fttr_miniolt_one(c->directory, request->id, &u->hardware);
		if (ret) {
			*fatal = ret;
			return ret;
		}
		u->phase = request->action != FTTR_ADMIN_RESUME ? ONU_BLOCKED : ONU_ABSENT;
		if (request->action == FTTR_ADMIN_DEACTIVATE) {
			memcpy(u->admin_serial, request->serial, 8);
			u->admin_pending = true;
			u->admin_timeout = now_ms() + 15000;
		}
	}
	*fatal = forwarding(c, true);
	if (!*fatal)
		*fatal = publish(c, "running", 0);
	return *fatal ? *fatal : ret;
}

static int move_finish(struct controller *c, int result)
{
	int32_t reply = result;
	struct onu *u = &c->onu[c->move.request.id];

	if (result && u->phase == ONU_MOVE) {
		fttr_services_cancel(&u->services, result);
		u->phase = ONU_SERVICE_FAILED;
		if (u->service_started)
			save_service(c, c->move.request.id);
	}

	if (c->move.client_length && sendto(c->control, &reply, sizeof(reply), MSG_DONTWAIT | MSG_NOSIGNAL,
	    (struct sockaddr *)&c->move.client, c->move.client_length) < 0)
		fprintf(stderr, "FTTR move reply failed: %s\n", strerror(errno));
	memset(&c->move, 0, sizeof(c->move));
	return result;
}

static int move_drive(struct controller *c)
{
	const struct fttr_admin_request *request = &c->move.request;
	struct onu *u = &c->onu[request->id];
	struct fttr_onu_state target, expected = u->hardware;
	int ret, resume;

	if (stopping) {
		move_finish(c, -ECANCELED);
		return -EINTR;
	}
	memcpy(expected.serial, request->serial, 8);
	ret = fttr_miniolt_same(c->directory, request->id, &expected);
	if (!ret)
		ret = fttr_miniolt_one(c->directory, request->target, &target);
	if (!ret && (memcmp(target.serial, request->target_serial, 8) || (target.downstream & ((1U << 18) | (1U << 16)))))
		ret = -ESTALE;
	if (ret)
		return move_finish(c, ret);
	if (u->service_started) {
		ret = fttr_services_update(&u->services, &c->transaction, c->directory, &expected);
		if (ret < 0)
			return move_finish(c, ret);
		if (ret != 2) {
			if (ret == 1)
				ret = fttr_me_job_send(fttr_services_pending(&u->services), c->socket, &c->link, now_ms());
			if (!ret)
				ret = save_service(c, request->id);
			return ret ? move_finish(c, ret) : 0;
		}
	}
	ret = forwarding(c, false);
	if (!ret)
		ret = cancel(c, request->id, true);
	if (!ret)
		ret = cancel(c, request->target, true);
	if (ret)
		return move_finish(c, ret);
	c->onu[request->id].phase = c->onu[request->target].phase = ONU_ABSENT;
	if (stopping) {
		move_finish(c, -ECANCELED);
		return -EINTR;
	}
	ret = fttr_miniolt_move(c->directory, request->id, request->target, request->serial, request->target_serial);
	/* Only pre-write stale/busy may resume FE. A partial reservation stops TX. */
	if (ret && ret != -ESTALE && ret != -EBUSY)
		return move_finish(c, ret);
	if (!ret) {
		u->phase = ONU_BLOCKED;
		u->admin_pending = true;
		memset(u->admin_serial, 0, sizeof(u->admin_serial));
		u->admin_timeout = now_ms() + 15000;
	}
	resume = forwarding(c, true);
	move_finish(c, ret ? ret : resume);
	return resume ? resume : publish(c, "running", 0);
}

static int move_request(struct controller *c, const struct fttr_admin_request *request, int *fatal)
{
	struct fttr_onu_state source, target;
	struct onu *u;
	int ret;

	*fatal = 0;
	if (request->version != 2 || request->action != FTTR_ADMIN_MOVE ||
	    request->id >= FTTR_ONUS || request->target >= FTTR_ONUS || request->id == request->target)
		return -EINVAL;
	if (c->upgrade.active) return -EBUSY;
	if (c->move.active || (c->reboot.active &&
	    (request->id == c->reboot.request.id || request->target == c->reboot.request.id)))
		return -EBUSY;
	ret = fttr_miniolt_one(c->directory, request->id, &source);
	if (!ret)
		ret = fttr_miniolt_one(c->directory, request->target, &target);
	if (ret)
		return ret;
	if (memcmp(source.serial, request->serial, 8) || memcmp(target.serial, request->target_serial, 8) ||
	    !(source.downstream & (1U << 18)) || (source.downstream & (1U << 16)))
		return -ESTALE;
	if (target.downstream & ((1U << 18) | (1U << 16)))
		return -EBUSY;
	u = &c->onu[request->id];
	if (memcmp(u->identity, source.serial, 8))
		return -ESTALE;
	if (u->service_started) {
		ret = fttr_services_release_ready(&u->services);
		if (ret)
			return ret;
	} else if (u->phase != ONU_INVENTORY && u->phase != ONU_CONFIGURED)
		return -EBUSY;
	else {
		const struct fttr_mib_entity *entity;

		/* An inventory-only controller cannot retire another owner's
		 * remote GEMs or recover the prior Alloc-ID from current values.
		 */
		for (entity = u->model.entities; entity; entity = entity->next) {
			size_t size;
			const uint8_t *value;

			if (entity->class_id == 268 || entity->class_id == 266)
				return -ENODATA;
			if (entity->class_id != 262)
				continue;
			value = fttr_mib_entity_attr(entity, 0x8000, &size);
			if (!value || size != 2 || (value[0] != 0 && value[0] != 255) || value[1] != 255)
				return -ENODATA;
		}
	}
	ret = forwarding(c, false);
	if (!ret)
		ret = withdraw_service(c, request->id, true);
	if (!ret)
		ret = cancel(c, request->target, true);
	if (!ret && u->service_started) {
		ret = fttr_services_release(&u->services, &c->transaction);
		if (ret > 0)
			ret = 0;
	}
	if (!ret)
		ret = forwarding(c, true);
	if (ret) {
		*fatal = ret;
		return ret;
	}
	u->admin_pending = c->onu[request->target].admin_pending = false;
	fttr_auth_cancel(&u->auth, -ECANCELED);
	u->policy_authenticated = u->policy_move = false;
	u->sync.active = u->sync.enabled = u->sync.valid = false;
	u->service_ready = false;
	u->phase = ONU_MOVE;
	c->move.active = true;
	c->move.request = *request;
	ret = publish(c, "running", 0);
	if (ret) {
		*fatal = move_finish(c, ret);
		return ret;
	}
	return -EINPROGRESS;
}

static int policy_move(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	struct fttr_admin_request request = { .version = 2, .action = FTTR_ADMIN_MOVE,
		.id = id, .target = u->selection.id };
	struct fttr_onu_state target;
	int ret, fatal;

	if (c->move.active)
		return 0;
	/* A different registration may have bound this formerly empty SN
	 * while the source MIB upload was in flight. Never overwrite it.
	 */
	if (c->onu_policy.rows[request.target].expected.serial[0] &&
	    memcmp(c->onu_policy.rows[request.target].expected.serial, u->identity, 8)) {
		u->policy_error = -ESTALE;
		return auth_failed(c, id, -ESTALE);
	}
	ret = fttr_miniolt_one(c->directory, request.target, &target);
	if (ret)
		return ret;
	memcpy(request.serial, u->identity, 8);
	memcpy(request.target_serial, target.serial, 8);
	ret = move_request(c, &request, &fatal);
	if (fatal)
		return fatal;
	if (ret == -EINPROGRESS)
		return move_drive(c);
	u->policy_error = ret;
	return auth_failed(c, id, ret);
}

/* The command is sent once. An ACK proves acceptance, not a restart. Keep
 * service admission withdrawn until departure/replacement is observed or
 * an explicit administrator resume starts fresh inventory/authentication.
 */
static int reboot_finish(struct controller *c, int result)
{
	unsigned int id = c->reboot.request.id;
	struct onu *u = &c->onu[id];
	int32_t reply = result;
	int ret = 0;

	u->reboot_error = result;
	if (result) {
		u->reboot.state = FTTR_ME_FAILED;
		u->reboot.error = result;
	}
	u->phase = u->reboot.attempts && !(u->reboot.replied && u->reboot.result) ?
		ONU_REBOOT_WAIT : ONU_ABSENT;
	if (u->grant) {
		ret = fttr_miniolt_management(c->directory, id, false);
		if (!ret) u->grant = false;
	}
	c->reboot.active = false;
	if (!ret && !stopping) ret = forwarding(c, true);
	if (ret) reply = ret;
	if (c->reboot.client_length && sendto(c->control, &reply, sizeof(reply), MSG_DONTWAIT | MSG_NOSIGNAL,
	    (struct sockaddr *)&c->reboot.client, c->reboot.client_length) < 0)
		fprintf(stderr, "FTTR reboot reply failed: %s\n", strerror(errno));
	memset(&c->reboot, 0, sizeof(c->reboot));
	if (!ret && !stopping) ret = publish(c, "running", 0);
	return ret;
}

static int reboot_drive(struct controller *c)
{
	unsigned int id = c->reboot.request.id;
	struct onu *u = &c->onu[id];
	struct fttr_onu_state expected = u->hardware;
	int ret;

	memcpy(expected.serial, c->reboot.request.serial, 8);
	ret = fttr_miniolt_same(c->directory, id, &expected);
	if (ret) return reboot_finish(c, ret);
	if (u->reboot.state != FTTR_ME_WAIT)
		return reboot_finish(c, u->reboot.error);
	if (!u->reboot.attempts)
		ret = fttr_me_job_send(&u->reboot, c->socket, &c->link, now_ms());
	else
		ret = fttr_me_job_timeout(&u->reboot, c->socket, &c->link, now_ms());
	return ret ? reboot_finish(c, ret) : 0;
}

static int reboot_request(struct controller *c, const struct fttr_admin_request *request, int *fatal)
{
	struct fttr_onu_state observed;
	struct onu *u;
	int ret;

	*fatal = 0;
	if (request->version != 1 || request->action != FTTR_ADMIN_REBOOT || request->id >= FTTR_ONUS)
		return -EINVAL;
	if (c->upgrade.active) return -EBUSY;
	if (c->reboot.active || (c->move.active &&
	    (request->id == c->move.request.id || request->id == c->move.request.target)))
		return -EBUSY;
	u = &c->onu[request->id];
	if (u->phase == ONU_REBOOT_WAIT || u->admin_pending) return -EBUSY;
	ret = fttr_miniolt_one(c->directory, request->id, &observed);
	if (ret) return ret;
	if (memcmp(observed.serial, request->serial, 8) || memcmp(u->identity, request->serial, 8) ||
	    !(observed.downstream & (1U << 18)) || (observed.downstream & (1U << 16)))
		return -ESTALE;
	if (!u->grant) return -ENOLINK;
	ret = forwarding(c, false);
	if (!ret) ret = cancel_state(c, request->id, true, true);
	if (ret) { *fatal = ret; return ret; }
	u->hardware = observed;
	u->phase = ONU_REBOOT;
	u->reboot_error = 0;
	/* Factory ASIC and FPGA1 both use ONU-G 256/0, operation 0x59,
	 * zero condition/body. omci-me marks this operation non-retryable.
	 */
	fttr_me_job_start(&u->reboot, request->id, &c->transaction, FTTR_ME_REBOOT, 256, 0, 0, NULL, 0);
	c->reboot.active = true;
	c->reboot.request = *request;
	return -EINPROGRESS;
}

static int upgrade_finish(struct controller *c, int result)
{
	unsigned int id = c->upgrade.request.id;
	struct onu *u = &c->onu[id];
	int32_t reply = result;
	int ret = 0;

	if (result) fttr_upgrade_cancel(&u->upgrade, result);
	u->phase = ONU_UPGRADE_WAIT;
	if (u->grant) {
		ret = fttr_miniolt_management(c->directory, id, false);
		if (!ret) u->grant = false;
	}
	free((void *)u->upgrade.data);
	u->upgrade.data = NULL;
	c->upgrade.active = false;
	if (!ret && !stopping) ret = forwarding(c, true);
	if (ret) reply = ret;
	if (c->upgrade.client_length && sendto(c->control, &reply, sizeof(reply), MSG_DONTWAIT | MSG_NOSIGNAL,
	    (struct sockaddr *)&c->upgrade.client, c->upgrade.client_length) < 0)
		fprintf(stderr, "FTTR upgrade reply failed: %s\n", strerror(errno));
	memset(&c->upgrade, 0, sizeof(c->upgrade));
	if (!ret && !stopping) ret = publish(c, "running", 0);
	return ret;
}

static int upgrade_drive(struct controller *c)
{
	unsigned int id = c->upgrade.request.id;
	struct onu *u = &c->onu[id];
	struct fttr_onu_state expected = u->hardware;
	int ret;

	memcpy(expected.serial, c->upgrade.request.serial, 8);
	ret = fttr_miniolt_same(c->directory, id, &expected);
	if (ret) return upgrade_finish(c, ret);
	if (u->upgrade.stage == FTTR_UPGRADE_DONE || u->upgrade.stage == FTTR_UPGRADE_FAILED)
		return upgrade_finish(c, u->upgrade.error);
	ret = fttr_upgrade_tick(&u->upgrade, &c->transaction, now_ms());
	if (ret < 0) return upgrade_finish(c, ret);
	ret = fttr_upgrade_send(&u->upgrade, &c->transaction, c->socket, &c->link, now_ms());
	return ret ? upgrade_finish(c, ret) : 0;
}

/* Copy the received regular-file descriptor once. Subsequent windows and
 * retries use this immutable snapshot, independent of the client's lifetime.
 */
static int upgrade_image(int fd, uint8_t **data, size_t *size)
{
	struct stat before, after;
	size_t offset = 0;
	uint8_t *buffer;
	int error;

	if (fstat(fd, &before)) return -errno;
	if (!S_ISREG(before.st_mode) || before.st_size <= 0 || (uint64_t)before.st_size > UINT32_MAX)
		return -EINVAL;
	buffer = malloc(before.st_size);
	if (!buffer) return -ENOMEM;
	while (offset < (size_t)before.st_size) {
		ssize_t got = pread(fd, buffer + offset, (size_t)before.st_size - offset, offset);
		if (got <= 0) { error = got < 0 ? -errno : -EIO; goto fail; }
		offset += got;
	}
	if (fstat(fd, &after)) { error = -errno; goto fail; }
	if (before.st_size != after.st_size || before.st_mtim.tv_sec != after.st_mtim.tv_sec ||
	    before.st_mtim.tv_nsec != after.st_mtim.tv_nsec || before.st_ctim.tv_sec != after.st_ctim.tv_sec ||
	    before.st_ctim.tv_nsec != after.st_ctim.tv_nsec) { error = -ESTALE; goto fail; }
	*data = buffer;
	*size = offset;
	return 0;
fail:
	free(buffer);
	return error;
}

static int upgrade_request(struct controller *c, const struct fttr_admin_request *request, int image_fd, int *fatal)
{
	struct fttr_onu_state observed;
	struct onu *u;
	uint8_t *data = NULL;
	size_t size = 0;
	int ret;

	*fatal = 0;
	if (request->version != 3 || request->id >= FTTR_ONUS || request->target ||
	    memcmp(request->target_serial, (uint8_t[8]){0}, 8) || request->action < FTTR_ADMIN_DOWNLOAD ||
	    request->action > FTTR_ADMIN_UPGRADE_ABORT ||
	    ((request->action == FTTR_ADMIN_DOWNLOAD) != (image_fd >= 0))) return -EINVAL;
	if (request->action == FTTR_ADMIN_UPGRADE_ABORT) {
		if (!c->upgrade.active || c->upgrade.request.id != request->id ||
		    memcmp(c->upgrade.request.serial, request->serial, 8)) return -ESTALE;
		*fatal = upgrade_finish(c, -ECANCELED);
		return *fatal;
	}
	if (c->upgrade.active || c->reboot.active || c->move.active) return -EBUSY;
	u = &c->onu[request->id];
	if (u->admin_pending || u->phase == ONU_REBOOT_WAIT) return -EBUSY;
	ret = fttr_miniolt_one(c->directory, request->id, &observed);
	if (ret) return ret;
	if (memcmp(observed.serial, request->serial, 8) || memcmp(u->identity, request->serial, 8) ||
	    !(observed.downstream & (1U << 18)) || (observed.downstream & (1U << 16))) return -ESTALE;
	if (image_fd >= 0) {
		ret = upgrade_image(image_fd, &data, &size);
		if (ret) return ret;
	}
	ret = forwarding(c, false);
	if (!ret) ret = cancel_state(c, request->id, true, true);
	if (!ret && !u->grant) {
		ret = fttr_miniolt_management(c->directory, request->id, true);
		if (!ret) u->grant = true;
	}
	if (ret) { free(data); *fatal = ret; return ret; }
	u->hardware = observed;
	ret = fttr_upgrade_start(&u->upgrade, request->id, &c->transaction,
		request->action - FTTR_ADMIN_DOWNLOAD, data, size);
	if (ret) { free(data); *fatal = ret; return ret; }
	u->phase = ONU_UPGRADE;
	c->upgrade.active = true;
	c->upgrade.request = *request;
	return -EINPROGRESS;
}

static int receive_control(struct controller *c)
{
	struct fttr_admin_request request = { 0 };
	struct sockaddr_un address;
	struct iovec vector = { .iov_base = &request, .iov_len = sizeof(request) };
	union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(struct ucred)) + CMSG_SPACE(sizeof(int) * 2)]; } credentials;
	struct msghdr message = { .msg_name = &address, .msg_namelen = sizeof(address),
		.msg_iov = &vector, .msg_iovlen = 1, .msg_control = credentials.bytes,
		.msg_controllen = sizeof(credentials.bytes) };
	struct cmsghdr *header;
	bool authorized = false;
	int32_t result;
	int fatal = 0, image_fd = -1;
	unsigned int fd_count = 0;
	ssize_t size = recvmsg(c->control, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);

	if (size < 0)
		return errno == EAGAIN ? 0 : -errno;
	for (header = CMSG_FIRSTHDR(&message); header; header = CMSG_NXTHDR(&message, header)) {
		if (header->cmsg_level == SOL_SOCKET && header->cmsg_type == SCM_CREDENTIALS &&
		    header->cmsg_len == CMSG_LEN(sizeof(struct ucred))) {
			struct ucred *peer = (void *)CMSG_DATA(header);

			authorized = peer->uid == geteuid();
		}
		if (header->cmsg_level == SOL_SOCKET && header->cmsg_type == SCM_RIGHTS) {
			size_t count = (header->cmsg_len - CMSG_LEN(0)) / sizeof(int);
			int *fds = (void *)CMSG_DATA(header);
			for (size_t n = 0; n < count; n++) {
				if (!fd_count++) image_fd = fds[n];
				else close(fds[n]);
			}
		}
	}
	if (!((request.version == 1 && size == offsetof(struct fttr_admin_request, target)) ||
	      ((request.version == 2 || request.version == 3) && size == sizeof(request))) ||
	    fd_count > 1 || (fd_count && (request.version != 3 || request.action != FTTR_ADMIN_DOWNLOAD)) ||
	    message.msg_flags & (MSG_TRUNC | MSG_CTRUNC))
		result = -EINVAL;
	else if (!authorized)
		result = -EACCES;
	else if (request.version == 3)
		result = upgrade_request(c, &request, image_fd, &fatal);
	else if (request.version == 2)
		result = move_request(c, &request, &fatal);
	else if (request.action == FTTR_ADMIN_REBOOT)
		result = reboot_request(c, &request, &fatal);
	else
		result = admin_request(c, &request, false, &fatal);
	if (image_fd >= 0) close(image_fd);
	if (result == -EINPROGRESS && request.version == 3) {
		c->upgrade.client = address;
		c->upgrade.client_length = message.msg_namelen;
		return upgrade_drive(c);
	}
	if (result == -EINPROGRESS && request.action == FTTR_ADMIN_REBOOT) {
		c->reboot.client = address;
		c->reboot.client_length = message.msg_namelen;
		return reboot_drive(c);
	}
	if (result == -EINPROGRESS && request.version == 2) {
		c->move.client = address;
		c->move.client_length = message.msg_namelen;
		return move_drive(c);
	}
	/* Client disappearance does not undo an already completed operation. */
	if (sendto(c->control, &result, sizeof(result), MSG_DONTWAIT | MSG_NOSIGNAL,
		   (struct sockaddr *)&address, message.msg_namelen) < 0)
		fprintf(stderr, "FTTR control reply failed: %s\n", strerror(errno));
	return fatal;
}

/* Original FPGA polling: 64*1ms + 36ms between scans; ASIC offline polling
 * runs every four 2s iterations and also has a 15s deactivation callback.
 * Error recovery and temporary block release share that scan cadence.
 */
static int admin_tick(struct controller *c, int64_t now)
{
	struct fttr_onu_state table[FTTR_ONUS];
	bool scan = now >= c->admin_scan_due, changed = false;
	unsigned int id;
	int ret;

	if (scan) {
		ret = fttr_miniolt_table(c->directory, table);
		if (ret)
			return ret;
	}
	for (id = 0; id < FTTR_ONUS; id++) {
		struct onu *u = &c->onu[id];

		if (c->move.active && (id == c->move.request.id || id == c->move.request.target))
			continue;
		if (u->phase == ONU_REBOOT || u->phase == ONU_REBOOT_WAIT ||
		    u->phase == ONU_UPGRADE || u->phase == ONU_UPGRADE_WAIT)
			continue;
		if (scan && !u->admin_pending && !(table[id].downstream & (1U << 16)) &&
		    fttr_miniolt_recovery_needed(c->backend, table[id].upstream)) {
			struct fttr_admin_request request = { .version = 1, .id = id,
				.action = FTTR_ADMIN_DEACTIVATE };
			int fatal;

			memcpy(request.serial, table[id].serial, 8);
			ret = admin_request(c, &request, true, &fatal);
			if (fatal || (ret && ret != -ESTALE && ret != -EAGAIN))
				return fatal ? fatal : ret;
			/* Original FPGA skips the remainder of this ONU's pass after
			 * recovery. ASIC rereads DS and may release in the same pass.
			 */
			if (ret || c->backend == FTTR_FPGA1)
				continue;
		}
		if (!u->admin_pending || (!scan &&
		    (c->backend == FTTR_FPGA1 || now < u->admin_timeout)))
			continue;
		/* The initial command already retired the software service owners.
		 * Releasing bit16 must never race an active service for this ONU.
		 */
		if (u->ports[0].index || u->service_started || u->grant || u->wifi)
			return -EUCLEAN;
		ret = fttr_miniolt_admin(c->directory, id, 2, u->admin_serial);
		if (ret == -EAGAIN)
			continue;
		if (ret && ret != -ESTALE)
			return ret;
		u->admin_pending = false;
		u->phase = ONU_ABSENT;
		ret = fttr_miniolt_one(c->directory, id, &u->hardware);
		if (ret)
			return ret;
		changed = true;
	}
	/* MDIO and recovery writes can take longer than a poll interval. Start
	 * the next interval after this pass, leaving time for OMCI/stop events.
	 */
	if (scan)
		c->admin_scan_due = now_ms() + (c->backend == FTTR_FPGA1 ? 100 : 8000);
	return changed ? publish(c, "running", 0) : 0;
}

static int run(struct controller *c)
{
	struct pollfd descriptors[2] = { { .fd = c->socket, .events = POLLIN },
		{ .fd = c->control, .events = POLLIN } };
	int64_t next_refresh = 0, now, deadline;
	unsigned int id;
	int ret;

	c->admin_scan_due = now_ms() + (c->backend == FTTR_FPGA1 ? 100 : 8000);
	while (!stopping) {
		now = now_ms();
		ret = fttr_miniolt_optical_tick(&c->optical, c->directory, now);
		if (ret)
			return ret;
		ret = admin_tick(c, now);
		if (ret)
			return ret;
		if (now >= next_refresh) {
			ret = refresh(c);
			if (ret)
				return ret;
			next_refresh = now_ms() + 2000;
		}
		now = now_ms();
		deadline = next_refresh;
		if (c->admin_scan_due < deadline)
			deadline = c->admin_scan_due;
		if (c->optical.interval && c->optical.due < deadline)
			deadline = c->optical.due;
		for (id = 0; id < FTTR_ONUS; id++) {
			struct onu *u = &c->onu[id];

			if (u->phase == ONU_UPGRADE) {
				ret = upgrade_drive(c);
				if (ret) return ret;
				if (c->upgrade.active) {
					int64_t due = u->upgrade.waiting || u->upgrade.stage == FTTR_UPGRADE_END_DELAY ?
						u->upgrade.me.deadline : now;
					if (due < deadline) deadline = due;
				}
				continue;
			}
			if (u->phase == ONU_UPGRADE_WAIT) continue;
			if (u->phase == ONU_REBOOT) {
				ret = reboot_drive(c);
				if (ret) return ret;
				if (c->reboot.active && u->reboot.deadline < deadline)
					deadline = u->reboot.deadline;
				continue;
			}
			if (u->phase == ONU_REBOOT_WAIT) continue;
			if (u->admin_pending) {
				if (c->admin_scan_due < deadline)
					deadline = c->admin_scan_due;
				if (c->backend != FTTR_FPGA1 && u->admin_timeout < deadline)
					deadline = u->admin_timeout;
				continue;
			}
			if (u->phase == ONU_INVENTORY && u->policy_move) {
				ret = policy_move(c, id);
				if (ret)
					return ret;
			}
			if (u->phase == ONU_AUTH_FAILED && u->auth_retry) {
				if (now >= u->auth_retry) {
					struct fttr_onu_state expected = u->hardware;

					memcpy(expected.serial, u->identity, 8);
					ret = fttr_miniolt_same(c->directory, id, &expected);
					if (ret && ret != -ESTALE)
						return ret;
					u->auth_retry = 0;
					if (!ret) {
						ret = cancel(c, id, false);
						if (!ret)
							ret = start_mib(c, id);
						if (!ret)
							ret = publish(c, "running", 0);
						if (ret)
							return ret;
					}
				} else if (u->auth_retry < deadline)
					deadline = u->auth_retry;
			}
			if (u->phase == ONU_MOVE) {
				struct fttr_me_job *me = fttr_services_pending(&u->services);

				if (me && now >= me->deadline) {
					struct fttr_onu_state target, expected = u->hardware;

					memcpy(expected.serial, c->move.request.serial, 8);
					ret = fttr_miniolt_same(c->directory, id, &expected);
					if (!ret)
						ret = fttr_miniolt_one(c->directory, c->move.request.target, &target);
					if (!ret && (memcmp(target.serial, c->move.request.target_serial, 8) ||
					    (target.downstream & ((1U << 18) | (1U << 16)))))
						ret = -ESTALE;
					if (ret)
						return move_finish(c, ret);
					ret = fttr_me_job_timeout(me, c->socket, &c->link, now);
					if (ret && ret != -ETIMEDOUT)
						return move_finish(c, ret);
				}
				ret = move_drive(c);
				if (ret)
					return ret;
				if (c->move.active && me && me->deadline < deadline)
					deadline = me->deadline;
				continue;
			}
			if (u->phase == ONU_AUTH) {
				struct fttr_me_job *me = &u->auth.me;
				struct fttr_onu_state expected = u->hardware;

				if (now >= me->deadline) {
					memcpy(expected.serial, u->identity, 8);
					ret = fttr_miniolt_same(c->directory, id, &expected);
					if (ret && ret != -ESTALE)
						return ret;
					if (ret)
						fttr_auth_cancel(&u->auth, ret);
					else {
						ret = fttr_me_job_timeout(me, c->socket, &c->link, now);
						if (ret && ret != -ETIMEDOUT)
							return ret;
					}
					if (ret) {
						ret = auth_drive(c, id);
						if (!ret)
							ret = publish(c, "running", 0);
						if (ret)
							return ret;
					}
				}
				if (u->phase == ONU_AUTH && me->deadline < deadline)
					deadline = me->deadline;
				continue;
			}
			if (u->phase == ONU_WIFI && u->wifi_current) {
				struct fttr_me_job *me = &u->wifi_current->job.transaction.me;

				if (now >= me->deadline) {
					struct fttr_onu_state expected = u->hardware;

					memcpy(expected.serial, u->identity, 8);
					ret = fttr_miniolt_same(c->directory, id, &expected);
					if (ret && ret != -ESTALE)
						return wifi_transport_failed(c, id, ret);
					if (!ret) {
						ret = fttr_me_job_timeout(me, c->socket, &c->link, now);
						if (ret && ret != -ETIMEDOUT)
							return wifi_transport_failed(c, id, ret);
					}
					ret = wifi_drive(c, id);
					if (!ret)
						ret = publish(c, "running", 0);
					if (ret)
						return ret;
				}
				if (u->wifi_current && u->wifi_current->job.transaction.me.deadline < deadline)
					deadline = u->wifi_current->job.transaction.me.deadline;
				continue;
			}
			if (u->sync.enabled) {
				if (!u->sync.active && now >= u->sync.due) {
					ret = start_sync(c, id);
					if (ret)
						return ret;
				}
				if (u->sync.active && now >= u->sync.job.deadline) {
					struct fttr_onu_state expected = u->hardware;

					memcpy(expected.serial, u->identity, 8);
					ret = fttr_miniolt_same(c->directory, id, &expected);
					if (ret) {
						if (ret != -ESTALE)
							return sync_io_failed(c, id, ret);
						ret = sync_failed(c, id, ret);
					} else {
						int timeout = fttr_me_job_timeout(&u->sync.job, c->socket, &c->link, now);

						if (timeout && timeout != -ETIMEDOUT)
							return sync_io_failed(c, id, timeout);
						if (timeout)
							ret = sync_failed(c, id, timeout);
					}
					if (ret)
						return ret;
					ret = save_sync(c, id);
					if (!ret)
						ret = publish(c, "running", 0);
					if (ret)
						return ret;
				}
				if (u->sync.active && u->sync.job.deadline < deadline)
					deadline = u->sync.job.deadline;
				if (!u->sync.active && u->sync.due < deadline)
					deadline = u->sync.due;
				continue;
			}
			if (u->phase == ONU_SERVICE) {
				struct fttr_me_job *me = fttr_services_pending(&u->services);

				if (now >= me->deadline) {
					struct fttr_onu_state expected = u->hardware;

					memcpy(expected.serial, u->identity, 8);
					ret = fttr_miniolt_same(c->directory, id, &expected);
					if (ret && ret != -ESTALE)
						return service_transport_failed(c, id, ret);
					if (ret)
						fttr_services_cancel(&u->services, ret);
					else {
						int timeout = fttr_me_job_timeout(me, c->socket, &c->link, now);

						if (timeout && timeout != -ETIMEDOUT)
							return service_transport_failed(c, id, timeout);
					}
					if (ret) {
						u->phase = ONU_SERVICE_FAILED;
						ret = save_service(c, id);
					} else {
						ret = service_drive(c, id);
					}
					if (!ret)
						ret = publish(c, "running", 0);
					if (ret)
						return ret;
				}
				if (u->phase == ONU_SERVICE) {
					me = fttr_services_pending(&u->services);
					if (me->deadline < deadline)
						deadline = me->deadline;
				}
				continue;
			}
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
		ret = multicast_update(c);
		if (ret)
			return ret;
		ret = poll(descriptors, 2, deadline > now ? deadline - now : 0);
		if (ret < 0)
			return -errno;
		if ((descriptors[0].revents | descriptors[1].revents) & (POLLERR | POLLHUP | POLLNVAL))
			return -EIO;
		if (descriptors[1].revents & POLLIN) {
			ret = receive_control(c);
			if (ret)
				return ret;
			next_refresh = 0;
		}
		if (descriptors[0].revents & POLLIN) {
			ret = receive_message(c);
			if (ret)
				return ret;
		}
		ret = multicast_update(c);
		if (ret)
			return ret;
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
	const struct timespec optical_settle = { .tv_nsec = 650000000 };

#define BOOT_STEP(name, operation) do { \
	if (stopping) return -EINTR; \
	fprintf(stderr, "FTTR hardware: %s\n", name); \
	ret = (operation); if (ret) return ret; \
} while (0)
	BOOT_STEP("disable TX", fttr_miniolt_tx_disable(c->directory));
	BOOT_STEP("close netdev", interface_state(c, false, 0));
	if (c->backend == FTTR_ASIC9690) {
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
	} else {
		/* Original FPGA loader board forces P6 HSGMII before post-DONE
		 * register setup. Its bitstream supplies the GPON/peer PCS logic.
		 */
		BOOT_STEP("prepare host P6", fttr_miniolt_p6_prepare(c->ni, !strcmp(nway, "1")));
		BOOT_STEP("initialize FPGA", fttr_miniolt_command(c->directory, "fpga_init", "1\n"));
		if (c->backend == FTTR_FPGA2)
			BOOT_STEP("initialize FPGA2 controller", fttr_miniolt_command(c->directory, "fpga2_init", "5\n"));
		if (c->backend == FTTR_FPGA1)
			BOOT_STEP("authenticate FPGA", fttr_miniolt_command(c->directory, "fpga_auth", "1\n"));
		BOOT_STEP("settle optical reset", nanosleep(&optical_settle, NULL) ? -errno : 0);
		BOOT_STEP("calibrate optics", fttr_miniolt_command(optical, "calibration", "1\n"));
		BOOT_STEP("wait for PCS", fttr_miniolt_wait_links(c->directory, c->ni));
	}
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
	if (c->backend != FTTR_FPGA1)
		BOOT_STEP("configure DBA", fttr_miniolt_command(c->directory, "dba", "1\n"));
	if (c->auth.enabled || c->onu_policy.enabled)
		BOOT_STEP("clear unverified data grants", clear_auth_grants(c));
	BOOT_STEP("verify optical calibration", fttr_miniolt_calibrated(optical));
	BOOT_STEP("initialize optical maintenance", fttr_miniolt_optical_start(&c->optical, now_ms()));
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
	if (c->ni >= 0) {
		result = forwarding(c, false);
		if (result)
			fprintf(stderr, "FTTR forwarding withdrawal failed: %d\n", result);
		if (!ret)
			ret = result;
	}
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
	static struct controller c = { .directory = -1, .ni = -1, .store = -1, .socket = -1,
		.control = -1, .onu_policy = { .directory = -1 }, .net = { .fd = -1 }, .optical = { .directory = -1 } };
	struct sigaction action = { .sa_handler = stop_signal };
	bool owned = false, auth_loaded = false;
	unsigned int id;
	int ret = 0, result;
	uint16_t seed;

	while (argc >= 3 && (!strcmp(argv[argc - 2], "--services") || !strcmp(argv[argc - 2], "--wifi") ||
	       !strcmp(argv[argc - 2], "--loid-auth") || !strcmp(argv[argc - 2], "--onu-auth"))) {
		if (!strcmp(argv[argc - 2], "--loid-auth")) {
			if (auth_loaded) { ret = -EEXIST; goto out; }
			auth_loaded = true;
		}
		if (!strcmp(argv[argc - 2], "--onu-auth") && c.onu_policy.enabled) { ret = -EEXIST; goto out; }
		ret = !strcmp(argv[argc - 2], "--onu-auth") ? fttr_onu_policy_load(argv[argc - 1], &c.onu_policy) :
			!strcmp(argv[argc - 2], "--loid-auth") ? fttr_auth_config_read(argv[argc - 1], &c.auth) :
			!strcmp(argv[argc - 2], "--wifi") ? load_wifi_policies(&c, argv[argc - 1]) :
			load_policies(&c, argv[argc - 1]);
		argc -= 2;
		if (ret)
			goto out;
	}
	if ((argc != 5 && argc != 9) || strlen(argv[4]) >= IFNAMSIZ) {
		fprintf(stderr, "Usage: %s <MiniOLT-dir> <NI-dir> <state-dir> <interface> [optical-dir host-mode nway pon-mode] [--services policy-file] [--wifi policy-file] [--loid-auth private-file] [--onu-auth private-json]\n", argv[0]);
		ret = -EINVAL;
		goto out;
	}
	if (argc == 9 && ((strcmp(argv[6], "5") && strcmp(argv[6], "9")) ||
	    (strcmp(argv[7], "0") && strcmp(argv[7], "1")) ||
	    (strcmp(argv[8], "1") && strcmp(argv[8], "2") && strcmp(argv[8], "3"))))
		{ ret = -EINVAL; goto out; }
	c.interface = argv[4];
	sigaction(SIGTERM, &action, NULL);
	sigaction(SIGINT, &action, NULL);
	c.directory = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (c.directory < 0) { ret = -errno; goto out; }
	ret = fttr_miniolt_backend(c.directory);
	if (ret < 0)
		goto out;
	c.backend = ret;
	{
		const struct service_policy *policy;

		for (policy = c.policies; policy; policy = policy->next) {
			if (policy->config.factory && c.backend == FTTR_FPGA1) { ret = -EOPNOTSUPP; goto out; }
			ret = fttr_miniolt_alloc_id(c.backend, 0, policy->config.slot);
			if (ret < 0)
				goto out;
		}
	}
	/* A procd replacement waits for the preceding controller's teardown. */
	if (flock(c.directory, LOCK_EX | (argc == 5 ? LOCK_NB : 0))) { ret = -errno; goto out; }
	owned = true;
	c.ni = open(argv[2], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (c.ni < 0) { ret = -errno; goto out; }
	c.store = open(argv[3], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (c.store < 0) { ret = -errno; goto out; }
	ret = forwarding(&c, false);
	if (ret)
		goto out;
	ret = fttr_net_open(&c.net, c.interface);
	if (ret)
		goto out;
	ret = fttr_net_mcast_reset(&c.net);
	if (!ret)
		ret = fttr_miniolt_command(c.ni, "fttr_mcast_flush", "1\n");
	if (ret)
		goto out;
	for (id = 0; id < FTTR_ONUS; id++) {
		ret = fttr_net_reset(&c.net, id);
		if (!ret)
			ret = remove_file(c.store, id, "mib");
		if (!ret)
			ret = remove_file(c.store, id, "pending");
		if (!ret)
			ret = archive_state(&c, id, "service");
		if (!ret)
			ret = archive_state(&c, id, "sync");
		if (!ret)
			ret = archive_state(&c, id, "wifi");
		if (ret)
			goto out;
	}
	if (unlinkat(c.store, "status.json", 0) && errno != ENOENT) { ret = -errno; goto out; }
	if ((c.auth.enabled || c.onu_policy.enabled) && argc == 5) {
		ret = clear_auth_grants(&c);
		if (ret)
			goto out;
	}
	if (argc == 9) {
		c.optical.directory = open(argv[5], O_RDONLY | O_DIRECTORY | O_CLOEXEC);

		if (c.optical.directory < 0) { ret = -errno; goto out; }
		ret = publish(&c, "initializing", 0);
		if (!ret)
			ret = bootstrap(&c, c.optical.directory, argv[6], argv[7], argv[8]);
		if (ret)
			goto out;
	}
	ret = fttr_miniolt_link(c.directory, &c.link);
	if (ret)
		goto out;
	if (getrandom(&seed, sizeof(seed), 0) < 0) { ret = -errno; goto out; }
	c.transaction = seed | 0x8000;
	ret = forwarding(&c, true);
	if (ret)
		goto out;
	c.socket = fttr_omci_open(c.interface, c.link.ethertype);
	if (c.socket < 0) { ret = c.socket; goto out; }
	ret = open_control(&c);
	if (ret)
		goto out;
	ret = run(&c);
out:
	if (stopping && ret == -EINTR)
		ret = 0;
	if (owned) {
		if (c.upgrade.active) {
			result = upgrade_finish(&c, ret ? ret : -ECANCELED);
			if (!ret) ret = result;
		}
		if (c.reboot.active) {
			result = reboot_finish(&c, ret ? ret : -ECANCELED);
			if (!ret) ret = result;
		}
		if (c.move.active)
			move_finish(&c, ret ? ret : -ECANCELED);
		if (c.control >= 0) {
			close(c.control);
			unlinkat(c.store, "control.sock", 0);
		}
		result = shutdown_hardware(&c);
		if (!ret)
			ret = result;
		for (id = 0; id < FTTR_ONUS; id++) {
			c.onu[id].admin_pending = false;
			c.onu[id].policy_authenticated = c.onu[id].policy_move = false;
			if (c.auth.enabled || c.onu_policy.enabled) {
				fttr_auth_cancel(&c.onu[id].auth, ret ? ret : -ECANCELED);
				if (c.onu[id].phase == ONU_AUTH)
					c.onu[id].phase = ONU_AUTH_FAILED;
			}
			if (c.store >= 0 && c.onu[id].wifi) {
				cancel_wifi(&c.onu[id], ret ? ret : -ECANCELED);
				result = save_wifi(&c, id);
				if (!ret)
					ret = result;
			}
			result = 0;
			for (unsigned int n = 0; n < c.onu[id].services.count; n++) {
				int cleanup = fttr_net_remove(&c.net, &c.onu[id].ports[n]);
				if (!result) result = cleanup;
			}
			if (result)
				c.onu[id].datapath_cleanup_error = result;
			if (!ret)
				ret = result;
			if (c.store >= 0 && c.onu[id].service_started) {
				result = save_service(&c, id);
				if (!ret)
					ret = result;
			}
		}
	}
	/* Publish the final collection counts/errors before freeing its ledgers
	 * and selected policy pointers. The stopped state is not data readiness. */
	if (c.store >= 0) {
		result = publish(&c, ret ? "failed" : "stopped", ret);
		if (!ret) ret = result;
	}
	for (id = 0; id < FTTR_ONUS; id++) {
		if (c.onu[id].dump && fclose(c.onu[id].dump) && !ret)
			ret = -errno;
		fttr_mib_model_clear(&c.onu[id].model);
		fttr_services_clear(&c.onu[id].services);
		while (c.onu[id].wifi) {
			struct wifi_run *next = c.onu[id].wifi->next;

			free(c.onu[id].wifi);
			c.onu[id].wifi = next;
		}
	}
	while (c.policies) {
		struct service_policy *next = c.policies->next;

		free(c.policies);
		c.policies = next;
	}
	while (c.wifi_policies) {
		struct wifi_policy *next = c.wifi_policies->next;

		free(c.wifi_policies);
		c.wifi_policies = next;
	}
	if (c.store >= 0) {
		close(c.store);
	}
	if (c.socket >= 0)
		close(c.socket);
	if (c.optical.directory >= 0)
		close(c.optical.directory);
	if (c.net.fd >= 0)
		close(c.net.fd);
	if (c.ni >= 0)
		close(c.ni);
	if (c.directory >= 0)
		close(c.directory);
	fttr_onu_policy_close(&c.onu_policy);
	if (ret)
		fprintf(stderr, "FTTR controller stopped: %s (%d)\n", strerror(-ret), ret);
	return ret ? 1 : 0;
}
