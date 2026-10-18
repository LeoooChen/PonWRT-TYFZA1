// SPDX-License-Identifier: GPL-2.0-only
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
#include <time.h>
#include <unistd.h>

#include "miniolt.h"
#include "fttr-net.h"
#include "omci-mib.h"
#include "omci-model.h"
#include "omci-service.h"
#include "omci-socket.h"

enum onu_phase { ONU_ABSENT, ONU_BLOCKED, ONU_MIB, ONU_INVENTORY, ONU_FAILED,
	ONU_SERVICE, ONU_CONFIGURED, ONU_SERVICE_FAILED, ONU_SYNC };

struct mib_sync {
	struct fttr_me_job job, failure;
	int64_t due, checked;
	int error;
	uint8_t value, observed;
	bool enabled, active, valid, recover, gem_probe;
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
	struct fttr_service_job service;
	bool service_started;
	uint16_t service_ordinal;
	struct mib_sync sync;
	struct fttr_net_port port;
	int datapath_error, datapath_cleanup_error;
	unsigned int rescans;
	enum onu_phase phase;
	uint8_t identity[8];
	bool grant;
	FILE *dump;
};

struct controller {
	int directory, ni, store, socket;
	struct fttr_omci_link link;
	struct fttr_net net;
	const char *interface;
	uint16_t transaction;
	int64_t observed_at;
	bool observed;
	uint64_t rx_errors;
	struct service_policy *policies;
	unsigned int policy_count;
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
	unsigned int number = 0;
	int ret = 0;

	if (!file)
		return -errno;
	while (getline(&line, &capacity, file) >= 0) {
		struct service_policy *policy, *other;
		char *token[11], *save, *p, *end;
		unsigned long value[8];
		unsigned int n = 0, i;

		number++;
		for (p = strtok_r(line, " \t\r\n", &save); p && n < 11;
		     p = strtok_r(NULL, " \t\r\n", &save)) {
			if (*p == '#')
				break;
			token[n++] = p;
		}
		if (!n)
			continue;
		if (n != 10 || !*token[9] || strlen(token[9]) >= IFNAMSIZ ||
		    strchr(token[9], '/') || strchr(token[9], ':') || strchr(token[9], '"') ||
		    strchr(token[9], '\\')) { ret = -EINVAL; break; }
		for (i = 0; i < 8; i++) {
			if (i == 4 && !strcmp(token[i + 1], "none")) {
				value[i] = 0;
				continue;
			}
			errno = 0;
			value[i] = strtoul(token[i + 1], &end, 0);
			if (errno || *end || value[i] > UINT16_MAX) { ret = -EINVAL; break; }
		}
		if (ret)
			break;
		if ((value[0] != 0 && value[0] != 2) || !value[2] || value[2] > 7 ||
		    value[3] > 7 || value[4] > 4094) { ret = -EINVAL; break; }
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
			    (policy->wildcard || !memcmp(policy->serial, other->serial, 8)))
				ret = -EEXIST;
		if (ret) { free(policy); break; }
		policy->config = (struct fttr_service_config) {
			.veip = value[0] == 2, .slot = value[2], .priority = value[3],
			.tagged = strcmp(token[5], "none") != 0, .vid = value[4],
			.fixed = value[5], .assured = value[6], .maximum = value[7]
		};
		policy->ordinal = value[1];
		if (strcmp(token[9], "-"))
			strcpy(policy->bridge, token[9]);
		policy->next = c->policies;
		c->policies = policy;
		c->policy_count++;
	}
	if (!ret && ferror(file))
		ret = -EIO;
	if (ret)
		fprintf(stderr, "FTTR policy %s:%u: %s (%d)\n", path, number, strerror(-ret), ret);
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

static int publish(struct controller *c, const char *state, int error)
{
	static const char * const phases[] = { "absent", "blocked", "mib", "inventory", "failed",
		"provision", "configured", "service-failed", "sync" };
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
		",\"rx_errors\":%" PRIu64 ",\"service_policies\":%u",
		state, stopping || error ? 0L : (long)getpid(), error, c->observed_at, c->rx_errors, c->policy_count);
	if (c->observed) {
		fputs(",\"onus\":[", file);
		for (id = 0; id < FTTR_ONUS; id++) {
			struct onu *u = &c->onu[id];

			fprintf(file, "%s{\"id\":%u,\"hardware_registered\":%s,\"phase\":\"%s\","
				"\"error\":%d,\"records\":%u,\"commands\":%u,\"entities\":%u,"
				"\"unparsed_records\":%u,\"serial\":\"",
				id ? "," : "", id, u->hardware.downstream & (1U << 18) ? "true" : "false",
				phases[u->phase], u->service_started ? u->service.error : u->job.error, u->job.sequence, u->job.commands,
				u->model.count, u->model.unparsed_records);
			hex(file, u->hardware.serial, 8);
			fputs("\",\"mib_serial\":\"", file);
			hex(file, u->identity, 8);
			fprintf(file, "\",\"service_configured\":%s,\"service_cleanup_error\":%d,"
				"\"mib_sync_valid\":%s,\"mib_sync\":%u,\"mib_sync_observed\":%u,"
				"\"mib_sync_error\":%d,\"mib_sync_checked_ms\":%" PRId64 ",\"mib_rescans\":%u,"
				"\"datapath_configured\":%s,\"datapath_error\":%d,\"datapath_cleanup_error\":%d}",
				u->service.configured ? "true" : "false", u->service.cleanup_error,
				u->sync.valid ? "true" : "false", u->sync.value, u->sync.observed,
				u->sync.error, u->sync.checked, u->rescans,
				u->port.configured ? "true" : "false", u->datapath_error, u->datapath_cleanup_error);
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
	fprintf(file, "{\"attempts\":%u,\"error\":%d,\"result\":%u,\"replied\":%s,\"request\":\"",
		me->attempts, me->error, me->result, me->replied ? "true" : "false");
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
		"\"error\":%d,\"checked_ms\":%" PRId64 ",\"rescans\":%u,\"exchange\":",
		sync->active ? "true" : "false", sync->valid ? "true" : "false", sync->value,
		sync->observed, sync->error, sync->checked, u->rescans);
	journal_me(file, &sync->job);
	fputs(",\"failure\":", file);
	journal_me(file, &sync->failure);
	fputs("}\n", file);
}

static int save_service(struct controller *c, unsigned int id)
{
	struct fttr_service_job *job = &c->onu[id].service;
	struct fttr_service_config *cfg = &job->config;
	struct fttr_me_job *me = fttr_service_pending(job);
	char temporary[24], destination[24];
	FILE *file;
	int fd, i;

	filename(temporary, id, "service.new");
	filename(destination, id, "service");
	fd = openat(c->store, temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	if (fd < 0)
		return -errno;
	file = fdopen(fd, "w");
	if (!file) { int ret = -errno; close(fd); return ret; }
	fttr_service_dump(job, file);
	fprintf(file, "{\"event\":\"service_datapath\",\"configured\":%s,\"interface\":\"%s\","
		"\"bridge\":\"%s\",\"ifindex\":%d,\"gem\":%u,\"mtu\":%u,\"error\":%d,\"cleanup_error\":%d}\n",
		c->onu[id].port.configured ? "true" : "false", c->onu[id].port.name,
		c->onu[id].port.bridge, c->onu[id].port.index, c->onu[id].port.gem,
		c->onu[id].port.mtu, c->onu[id].datapath_error, c->onu[id].datapath_cleanup_error);
	if (job->transaction.count)
		fttr_objects_job_dump(&job->transaction, file, "service_objects");
	fprintf(file, "{\"event\":\"service_recipe\",\"kind\":%u,\"uni\":%u,\"uni_ordinal\":%u,\"slot\":%u,"
		"\"priority\":%u,\"tagged\":%s,\"vid\":%u,\"bandwidth\":[%u,%u,%u],\"objects\":[",
		cfg->veip ? 2 : 0, cfg->uni, c->onu[id].service_ordinal, cfg->slot, cfg->priority, cfg->tagged ? "true" : "false",
		cfg->vid, cfg->fixed, cfg->assured, cfg->maximum);
	for (i = 0; i < job->transaction.count; i++) {
		struct fttr_omci_object *obj = &job->objects[i];

		fprintf(file, "%s{\"class\":%u,\"instance\":%u,\"dependencies\":%u,"
			"\"create_mask\":%u,\"create\":\"", i ? "," : "", obj->class_id,
			obj->instance, obj->dependencies, obj->create.mask);
		hex(file, obj->create.value, obj->create.size);
		fprintf(file, "\",\"set_mask\":%u,\"set\":\"", obj->set.mask);
		hex(file, obj->set.value, obj->set.size);
		fputs("\"}", file);
	}
	fputs("]}\n", file);
	if (me) {
		fputs("{\"event\":\"service_pending\",\"exchange\":", file);
		journal_me(file, me);
		fputs("}\n", file);
	}
	if (job->vlan.before.me.request[2]) {
		fprintf(file, "{\"event\":\"service_vlan\",\"sent\":%u,\"verified\":%s,"
			"\"restored\":%s,\"before\":", job->vlan.sent,
			job->vlan.verified ? "true" : "false", job->vlan.restored ? "true" : "false");
		journal_table(file, &job->vlan.before.table);
		fputs(",\"after\":", file);
		journal_table(file, &job->vlan.after.table);
		fputs(",\"failure\":", file);
		journal_me(file, &job->vlan.failure);
		fputs(",\"cleanup_failure\":", file);
		journal_me(file, &job->vlan.cleanup_failure);
		fputs("}\n", file);
	}
	if (c->onu[id].sync.enabled) {
		journal_sync(file, &c->onu[id], id);
	}
	if (fclose(file))
		return -errno;
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

	fttr_service_cancel(&c->onu[id].service, error);
	c->onu[id].phase = ONU_SERVICE_FAILED;
	ret = save_service(c, id);
	return ret ? ret : error;
}

static int start_sync(struct controller *c, unsigned int id);

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
			fttr_service_cancel(&u->service, ret);
			u->phase = ONU_SERVICE_FAILED;
			break;
		}
		action = fttr_service_update(&u->service, &c->transaction, c->directory, &expected);
		if (action != 1) {
			if (action < 0)
				u->phase = ONU_SERVICE_FAILED;
			else if (action == 2) {
				ret = fttr_miniolt_same(c->directory, id, &expected);
				if (ret && ret != -ESTALE)
					return service_transport_failed(c, id, ret);
				if (ret)
					fttr_service_cancel(&u->service, ret);
				u->phase = ret ? ONU_SERVICE_FAILED : ONU_SYNC;
				if (!ret) {
					u->service.configured = false;
					u->sync.enabled = true;
					return start_sync(c, id);
				}
			}
			break;
		}
		ret = save_service(c, id);
		if (ret)
			return ret;
		me = fttr_service_pending(&u->service);
		ret = fttr_me_job_send(me, c->socket, &c->link, now_ms());
		if (ret)
			return service_transport_failed(c, id, ret);
	}
	return save_service(c, id);
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

static int start_service(struct controller *c, unsigned int id)
{
	struct onu *u = &c->onu[id];
	const struct service_policy *match = matching_policy(c, u->identity);
	struct fttr_service_config config;
	const struct fttr_mib_entity *entity;
	unsigned int ordinal = 0;
	int ret;

	if (!match)
		return 0;
	config = match->config;
	for (entity = u->model.entities; entity; entity = entity->next)
		if (entity->class_id == (config.veip ? 329 : 11) && ordinal++ == match->ordinal)
			break;
	ret = archive_state(c, id, "service");
	if (ret)
		return ret;
	u->service_started = true;
	u->service_ordinal = match->ordinal;
	if (!entity) {
		u->service.phase = "select-uni";
		u->service.error = -ENOENT;
		u->service.transaction.me.onu = id;
		u->service.config = config;
		memcpy(u->service.transaction.serial, u->identity, 8);
		u->phase = ONU_SERVICE_FAILED;
		return save_service(c, id);
	}
	config.uni = entity->instance;
	ret = fttr_service_start(&u->service, id, &c->transaction, &u->model, &config, u->identity);
	if (ret) {
		u->phase = ONU_SERVICE_FAILED;
		return save_service(c, id);
	}
	u->phase = ONU_SERVICE;
	return service_drive(c, id);
}

static int withdraw_service(struct controller *c, unsigned int id)
{
	struct fttr_service_job *job = &c->onu[id].service;
	int ret, cleanup = fttr_net_remove(&c->net, &c->onu[id].port);

	if (cleanup)
		c->onu[id].datapath_cleanup_error = cleanup;

	if (!job->config.slot || job->grant_withdrawn)
		return cleanup;
	ret = fttr_miniolt_tcont(c->directory, id, job->config.slot, 0, 0, 0);
	if (!ret)
		ret = fttr_miniolt_tcont_read(c->directory, id, job->config.slot, &job->observed);
	if (!ret && (job->observed.fixed || job->observed.limits))
		ret = -EUCLEAN;
	if (ret) {
		job->cleanup_error = ret;
		save_service(c, id);
		return ret;
	}
	job->grant_withdrawn = true;
	return cleanup;
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
	if (u->service_started) {
		if (u->sync.active) {
			u->sync.job.state = FTTR_ME_FAILED;
			u->sync.job.error = -ESTALE;
			if (!u->sync.error)
				u->sync.error = -ESTALE;
		}
		u->sync.active = false;
		u->sync.valid = false;
		fttr_service_cancel(&u->service, -ESTALE);
		ret = withdraw_service(c, id);
		if (ret)
			return ret;
		ret = save_service(c, id);
		if (ret)
			return ret;
		ret = archive_state(c, id, "service");
		if (ret)
			return ret;
		fttr_service_clear(&u->service);
		memset(&u->service, 0, sizeof(u->service));
		u->service_started = false;
	}
	memset(&u->port, 0, sizeof(u->port));
	u->datapath_error = u->datapath_cleanup_error = 0;
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
	return 0;
}

static int failed(struct controller *c, unsigned int id, int error)
{
	struct onu *u = &c->onu[id];
	char pending[24], destination[24];
	bool watch = u->dump && matching_policy(c, u->identity);

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
	int ret;

	u->sync.active = false;
	u->sync.valid = false;
	u->sync.error = error;
	if (!u->sync.recover) {
		u->sync.failure = u->sync.job;
		u->sync.failure.state = FTTR_ME_FAILED;
		u->sync.failure.error = error;
	}
	u->sync.recover = true;
	u->sync.due = now_ms() + 2000;
	if (u->service_started) {
		fttr_service_cancel(&u->service, error);
		u->phase = ONU_SERVICE_FAILED;
		ret = withdraw_service(c, id);
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
	ret = cancel(c, id);
	if (ret)
		return ret;
	u->sync = previous;
	u->sync.enabled = false;
	u->sync.recover = true;
	ret = start_mib(c, id);
	if (!ret)
		u->rescans++;
	return ret;
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
	if (sync->gem_probe) {
		/* The fixed service plan's class268 CTP is object3; its complete
		 * selected attributes prove it survived when data-sync remains zero. */
		const struct fttr_omci_values *values = &u->service.objects[3].create;

		if ((sync->job.error == -EREMOTEIO && sync->job.result == 5) ||
		    (!sync->job.error && memcmp(sync->job.reply + 11, values->value, values->size))) {
			sync->error = -ESTALE;
			return rescan_sync(c, id);
		}
		if (sync->job.error)
			return sync_failed(c, id, sync->job.error);
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
		if (!sync->observed) {
			sync->gem_probe = true;
			fttr_me_job_start(&sync->job, id, &c->transaction, FTTR_ME_GET,
				268, u->service.gem, u->service.objects[3].create.mask, NULL, 0);
			return sync_send(c, id);
		}
	}
	sync->value = sync->observed;
	sync->valid = true;
	sync->error = 0;
	sync->checked = now_ms();
	sync->due = sync->checked + 2000;
	u->service.configured = true;
	{
		const struct service_policy *policy = matching_policy(c, u->identity);

		if (*policy->bridge) {
			ret = u->port.configured ? fttr_net_verify(&c->net, &u->port) :
				fttr_net_create(&c->net, &u->port, id, u->identity, u->service.gem, policy->bridge);
			if (ret) {
				u->datapath_error = ret;
				u->sync.enabled = false;
				fttr_service_cancel(&u->service, ret);
				u->phase = ONU_SERVICE_FAILED;
				ret = withdraw_service(c, id);
				return ret ? ret : save_service(c, id);
			}
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
	return start_service(c, id);
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
			if (u->sync.active &&
			    ((uint16_t)message.body[0] << 8 | message.body[1]) == u->sync.job.transaction) {
				u->sync.job.state = FTTR_ME_FAILED;
				u->sync.job.error = error;
				ret = sync_reply(c, message.onu);
				return ret ? ret : publish(c, "running", 0);
			}
			if (u->phase == ONU_SERVICE) {
				struct fttr_me_job *me = fttr_service_pending(&u->service);

				if (((uint16_t)message.body[0] << 8 | message.body[1]) == me->transaction) {
					me->state = FTTR_ME_FAILED;
					me->error = error;
					ret = service_drive(c, message.onu);
					if (ret)
						return ret;
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
	if (u->sync.active) {
		if (!fttr_me_job_reply(&u->sync.job, &message))
			return 0;
		ret = sync_reply(c, message.onu);
		return ret ? ret : publish(c, "running", 0);
	}
	if (u->phase == ONU_SERVICE) {
		if (!fttr_me_job_reply(fttr_service_pending(&u->service), &message))
			return 0;
		ret = service_drive(c, message.onu);
		return ret ? ret : publish(c, "running", 0);
	}
	sequence = u->job.sequence;
	ret = fttr_mib_job_reply(&u->job, &message, &c->transaction, &record);
	if (ret < 0) {
		fprintf(u->dump, "{\"event\":\"mib_reply_error\",\"error\":%d,\"body\":\"", ret);
		hex(u->dump, message.body, message.length);
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
				struct fttr_me_job *me = fttr_service_pending(&u->service);

				if (now >= me->deadline) {
					struct fttr_onu_state expected = u->hardware;

					memcpy(expected.serial, u->identity, 8);
					ret = fttr_miniolt_same(c->directory, id, &expected);
					if (ret && ret != -ESTALE)
						return service_transport_failed(c, id, ret);
					if (ret)
						fttr_service_cancel(&u->service, ret);
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
					me = fttr_service_pending(&u->service);
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
	struct controller c = { .directory = -1, .ni = -1, .store = -1, .socket = -1,
		.net = { .fd = -1 } };
	struct sigaction action = { .sa_handler = stop_signal };
	bool owned = false;
	unsigned int id;
	int ret = 0, result;
	uint16_t seed;

	if (argc >= 3 && !strcmp(argv[argc - 2], "--services")) {
		ret = load_policies(&c, argv[argc - 1]);
		argc -= 2;
		if (ret)
			goto out;
	}
	if ((argc != 5 && argc != 9) || strlen(argv[4]) >= IFNAMSIZ) {
		fprintf(stderr, "Usage: %s <MiniOLT-dir> <NI-dir> <state-dir> <interface> [optical-dir host-mode nway pon-mode] [--services policy-file]\n", argv[0]);
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
	/* A procd replacement waits for the preceding controller's teardown. */
	if (flock(c.directory, LOCK_EX | (argc == 5 ? LOCK_NB : 0))) { ret = -errno; goto out; }
	owned = true;
	c.ni = open(argv[2], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (c.ni < 0) { ret = -errno; goto out; }
	c.store = open(argv[3], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (c.store < 0) { ret = -errno; goto out; }
	ret = fttr_net_open(&c.net, c.interface);
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
	if (owned) {
		result = shutdown_hardware(&c);
		if (!ret)
			ret = result;
		for (id = 0; id < FTTR_ONUS; id++) {
			result = fttr_net_remove(&c.net, &c.onu[id].port);
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
	for (id = 0; id < FTTR_ONUS; id++) {
		if (c.onu[id].dump && fclose(c.onu[id].dump) && !ret)
			ret = -errno;
		fttr_mib_model_clear(&c.onu[id].model);
		fttr_service_clear(&c.onu[id].service);
	}
	while (c.policies) {
		struct service_policy *next = c.policies->next;

		free(c.policies);
		c.policies = next;
	}
	if (c.store >= 0) {
		result = publish(&c, ret ? "failed" : "stopped", ret);
		if (!ret)
			ret = result;
		close(c.store);
	}
	if (c.socket >= 0)
		close(c.socket);
	if (c.net.fd >= 0)
		close(c.net.fd);
	if (c.ni >= 0)
		close(c.ni);
	if (c.directory >= 0)
		close(c.directory);
	if (ret)
		fprintf(stderr, "FTTR controller stopped: %s (%d)\n", strerror(-ret), ret);
	return ret ? 1 : 0;
}
