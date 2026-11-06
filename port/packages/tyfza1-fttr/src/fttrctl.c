// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

#include "miniolt.h"
#include "fttr-control.h"
#include "omci-mib.h"
#include "omci-me.h"
#include "omci-gem.h"
#include "omci-association.h"
#include "omci-vlan.h"
#include "omci-service.h"
#include "omci-wifi.h"
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

static int optical_status(int directory)
{
	unsigned int model, voltage, bias, rx_supported;
	long long tx, rx;
	int temperature, fields, ret, fd;
	const char *name;
	char trailing;
	FILE *file;

	fd = openat(directory, "diagnostics", O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (fd < 0)
		return -errno;
	file = fdopen(fd, "r");
	if (!file) {
		ret = -errno;
		close(fd);
		return ret;
	}
	fields = fscanf(file, "model=%u temperature_q8=%d voltage_100uv=%u bias_2ua=%u "
		"tx_power_q8=%lld rx_power_q8=%lld rx_supported=%u %c",
		&model, &temperature, &voltage, &bias, &tx, &rx, &rx_supported, &trailing);
	ret = ferror(file) ? -(errno ? errno : EIO) : fields == 7 ? 0 : -EPROTO;
	fclose(file);
	if (ret)
		return ret;
	switch (model) {
	case 1: name = "UX3326"; break;
	case 2: name = "UX3326S"; break;
	case 10: name = "GN25L99"; break;
	case 20: name = "GN25L81"; break;
	default: return -EOPNOTSUPP;
	}
	if (temperature < -32768 || temperature > 32767 || voltage > 65535 || bias > 65535 ||
	    tx < 0 || tx > 2155871999LL || rx < 0 || rx > 65535 * 256 ||
	    rx_supported != (model == 2) || (!rx_supported && rx) ||
	    (model == 2 && tx > 65535 * 256))
		return -EPROTO;
	printf("{\"model\":\"%s\",\"temperature_c\":%.8f,\"voltage_v\":%.4f,\"bias_ma\":%.3f,"
	       "\"tx_power_uw\":%.9f,\"tx_power_dbm\":", name, temperature / 256.0,
	       voltage * 0.0001, bias * 0.002, tx / 2560.0);
	if (tx)
		printf("%.8f", 10 * log10(tx / 256.0) - 40);
	else
		fputs("null", stdout); /* Measured zero has no finite dBm representation. */
	printf(",\"rx_supported\":%s,\"rx_power_uw\":", rx_supported ? "true" : "false");
	if (rx_supported)
		printf("%.9f", rx / 2560.0);
	else
		fputs("null", stdout);
	fputs(",\"rx_power_dbm\":", stdout);
	if (rx_supported && rx)
		printf("%.8f", 10 * log10(rx / 256.0) - 40);
	else
		fputs("null", stdout);
	puts("}");
	return ferror(stdout) ? -EIO : 0;
}

static int open_session(int directory, unsigned int id, const char *interface,
			struct fttr_omci_session *session, struct fttr_onu_state *before)
{
	uint16_t seed;
	int ret;

	if (flock(directory, LOCK_EX | LOCK_NB))
		return -errno;
	ret = fttr_miniolt_one(directory, id, before);
	if (ret)
		return ret;
	if (!(before->downstream & (1U << 18)))
		return -ENOLINK;
	if (before->downstream & (1U << 16))
		return -EACCES;
	ret = fttr_miniolt_link(directory, &session->link);
	if (ret)
		return ret;
	ret = getrandom(&seed, sizeof(seed), 0);
	if (ret < 0)
		return -errno;
	session->onu = id;
	session->transaction = seed | 0x8000;
	session->fd = fttr_omci_open(interface, session->link.ethertype);
	if (session->fd < 0)
		return session->fd;
	ret = fttr_miniolt_management(directory, id, true);
	if (ret) {
		close(session->fd);
		return ret;
	}
	return 0;
}

static int scan(int directory, unsigned int id, const char *interface)
{
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	uint16_t commands;
	int ret = open_session(directory, id, interface, &session, &before);

	if (ret)
		return ret;
	printf("{\"event\":\"mib_start\",\"id\":%u,\"serial\":\"", id);
	hex(before.serial, sizeof(before.serial));
	puts("\"}");
	ret = fttr_mib_scan(&session, &commands, mib_record, NULL);
	close(session.fd);
	if (ret)
		return ret;
	ret = fttr_miniolt_same(directory, id, &before);
	if (ret)
		return ret;
	printf("{\"event\":\"mib_complete\",\"id\":%u,\"commands\":%u}\n", id, commands);
	return ferror(stdout) ? -EIO : 0;
}

static int get(int directory, unsigned int id, const uint16_t fields[3], const char *interface)
{
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_me_job job;
	int result, ret = open_session(directory, id, interface, &session, &before);

	if (ret)
		return ret;
	ret = fttr_me_job_start(&job, id, &session.transaction, FTTR_ME_GET,
				fields[0], fields[1], fields[2], NULL, 0);
	if (!ret)
		ret = fttr_me_exchange(&session, &job);
	close(session.fd);
	if (job.replied) {
		result = fttr_miniolt_same(directory, id, &before);
		if (result)
			return result;
		printf("{\"event\":\"me_reply\",\"id\":%u,\"class\":%u,\"instance\":%u,"
		       "\"mask\":%u,\"result\":%u,\"error\":%d,\"body\":\"",
		       id, fields[0], fields[1], fields[2], job.result, ret);
		hex(job.reply, sizeof(job.reply));
		puts("\"}");
	}
	return ferror(stdout) ? -EIO : ret;
}

static int tcont_exchange(struct fttr_omci_session *session, struct fttr_me_job *job,
			  uint16_t instance, const uint8_t *value)
{
	int ret = fttr_me_job_start(job, session->onu, &session->transaction,
				   value ? FTTR_ME_SET : FTTR_ME_GET, 262, instance, 0x8000,
				   value, value ? 2 : 0);

	return ret ? ret : fttr_me_exchange(session, job);
}

static int bind_tcont(int directory, unsigned int id, const uint16_t fields[5], const char *interface)
{
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_me_job job;
	uint16_t previous = 0, observed = 0;
	uint8_t value[2];
	bool read = false, sent = false, verified = false;
	const char *phase = "read";
	int alloc, ret = fttr_miniolt_backend(directory);

	if (ret < 0)
		return ret;
	alloc = fttr_miniolt_alloc_id(ret, id, fields[0]);
	if (alloc < 0)
		return alloc;
	value[0] = alloc >> 8;
	value[1] = alloc;
	ret = open_session(directory, id, interface, &session, &before);

	if (ret)
		return ret;
	ret = tcont_exchange(&session, &job, fields[1], NULL);
	if (ret)
		goto out;
	previous = observed = (uint16_t)job.reply[11] << 8 | job.reply[12];
	read = true;
	phase = "identity";
	ret = fttr_miniolt_same(directory, id, &before);
	if (ret)
		goto out;
	if (previous != alloc) {
		phase = "set";
		ret = tcont_exchange(&session, &job, fields[1], value);
		sent = job.attempts != 0;
		if (ret)
			goto out;
		phase = "verify";
		ret = tcont_exchange(&session, &job, fields[1], NULL);
		if (ret)
			goto out;
		observed = (uint16_t)job.reply[11] << 8 | job.reply[12];
		if (observed != alloc) {
			ret = -EUCLEAN;
			goto out;
		}
		phase = "identity";
		ret = fttr_miniolt_same(directory, id, &before);
		if (ret)
			goto out;
	}
	verified = true;
	phase = "grant";
	ret = fttr_miniolt_tcont(directory, id, fields[0], fields[2], fields[3], fields[4]);
	if (!ret)
		phase = "bound";
out:
	close(session.fd);
	printf("{\"event\":\"tcont_binding\",\"id\":%u,\"slot\":%u,\"instance\":%u,"
	       "\"alloc_id\":%u,\"phase\":\"%s\",\"error\":%d,\"remote_write_sent\":%s,"
	       "\"alloc_verified\":%s,\"previous_alloc_id\":",
	       id, fields[0], fields[1], alloc, phase, ret, sent ? "true" : "false", verified ? "true" : "false");
	if (read)
		printf("%u,\"last_read_alloc_id\":%u", previous, observed);
	else
		fputs("null,\"last_read_alloc_id\":null", stdout);
	if (job.replied) {
		printf(",\"result\":%u,\"body\":\"", job.result);
		hex(job.reply, sizeof(job.reply));
		fputc('"', stdout);
	}
	puts("}");
	return ferror(stdout) ? -EIO : ret;
}

static int create_gem(int directory, unsigned int id, const uint16_t fields[6], const char *interface)
{
	struct fttr_gem_config config = {
		.entity = fttr_miniolt_gem_id(id, fields[0]), .tcont = fields[1], .queue = fields[2],
		.bridge = fields[3], .profile = fields[4], .gal = fields[5],
	};
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_gem_job job;
	int result, ret;

	if (config.entity > 4090)
		return -ERANGE;
	ret = open_session(directory, id, interface, &session, &before);
	if (ret)
		return ret;
	fttr_gem_job_start(&job, id, &session.transaction, &config, before.serial);
	ret = fttr_objects_execute(&session, &job.transaction, directory, &before);
	close(session.fd);
	result = fttr_objects_job_dump(&job.transaction, stdout, "gem_state");
	return ret ? ret : result;
}

static int probe(struct fttr_omci_session *session, uint16_t class_id, uint16_t instance, uint16_t mask)
{
	struct fttr_me_job job;
	int ret;

	fttr_me_job_start(&job, session->onu, &session->transaction, FTTR_ME_GET,
			class_id, instance, mask, NULL, 0);
	ret = fttr_me_exchange(session, &job);
	if (ret && job.replied)
		fprintf(stderr, "FTTR ME %u/%u probe: result=%u error=%d\n", class_id, instance, job.result, ret);
	return ret;
}

static int associate(int directory, unsigned int id, const uint16_t *fields, bool profiles, const char *interface)
{
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_association_job job;
	int result, ret = open_session(directory, id, interface, &session, &before);

	if (ret)
		return ret;
	if (profiles) {
		struct fttr_profiles_config config = {
			.bridge = fields[0], .per_uni = fields[1], .use_mapper = fields[2], .mapper = fields[3],
		};

		fttr_profiles_job_start(&job, id, &session.transaction, &config, before.serial);
	} else {
		struct fttr_uni_config config = {
			.veip = fields[0] == 2, .uni = fields[1], .bridge = fields[2],
			.entity = fields[3], .ordinal = fields[4],
		};
		ret = probe(&session, config.veip ? 329 : 11, config.uni, config.veip ? 0x8000 : 0x0800);
		if (ret) {
			close(session.fd);
			return ret;
		}
		fttr_uni_job_start(&job, id, &session.transaction, &config, before.serial);
	}
	ret = fttr_objects_execute(&session, &job.transaction, directory, &before);
	close(session.fd);
	result = fttr_objects_job_dump(&job.transaction, stdout, profiles ? "profiles_state" : "uni_state");
	return ret ? ret : result;
}

static int forwarding(int directory, unsigned int id, const uint16_t *fields,
		      bool mapper, const char *vlans, const char *interface)
{
	struct fttr_ani_config ani = { 0 };
	struct fttr_mapper_config mapping = { 0 };
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_association_job job;
	unsigned int i;
	int result, ret;

	if (mapper) {
		mapping.entity = fields[0];
		mapping.priorities = fields[1];
		for (i = 0; i < 8; i++)
			mapping.interworking[i] = fields[2];
	} else {
		ani.entity = fields[0];
		ani.bridge = fields[1];
		ani.port = fields[2];
		ani.mapper = fields[3];
		ani.pointer = fields[4];
		if (strcmp(vlans, "none")) {
			const char *cursor = vlans;
			char *end;

			for (;;) {
				unsigned long vid;

				if (ani.count == 12)
					return -E2BIG;
				errno = 0;
				vid = strtoul(cursor, &end, 0);
				if (errno || end == cursor || vid > 4094 || (*end && *end != ','))
					return -EINVAL;
				ani.vlans[ani.count++] = vid;
				if (!*end)
					break;
				cursor = end + 1;
			}
		}
	}
	ret = open_session(directory, id, interface, &session, &before);
	if (ret)
		return ret;
	if (!mapper || fields[2] != 0xffff) {
		ret = probe(&session, !mapper && ani.mapper ? 130 : 266,
			mapper ? fields[2] : ani.pointer, 0x8000);
		if (ret) {
			close(session.fd);
			return ret;
		}
	}
	if (mapper)
		fttr_mapper_job_start(&job, id, &session.transaction, &mapping, before.serial);
	else
		fttr_ani_job_start(&job, id, &session.transaction, &ani, before.serial);
	ret = fttr_objects_execute(&session, &job.transaction, directory, &before);
	close(session.fd);
	result = fttr_objects_job_dump(&job.transaction, stdout, mapper ? "mapper_state" : "ani_state");
	return ret ? ret : result;
}

static void vlan_table_dump(const struct fttr_vlan_read_job *job)
{
	printf("{\"capacity\":%u,\"length\":%" PRIu32 ",\"received\":%" PRIu32
	       ",\"complete\":%s,\"error\":%d,\"data\":\"", job->table.capacity,
	       job->table.length, job->table.received, job->table.complete ? "true" : "false", job->error);
	hex(job->table.data, job->table.received);
	printf("\"}");
}

static void vlan_failure_dump(const struct fttr_me_job *me)
{
	printf("{\"attempts\":%u,\"error\":%d,\"replied\":%s,\"result\":%u,\"request\":\"",
	       me->attempts, me->error, me->replied ? "true" : "false", me->result);
	hex(me->request, sizeof(me->request));
	printf("\",\"reply\":\"");
	hex(me->reply, sizeof(me->reply));
	printf("\"}");
}

static int vlan_command(int directory, unsigned int id, const uint16_t fields[6],
			bool read_only, const char *interface)
{
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	struct fttr_vlan_change change;
	struct fttr_vlan_plan plan;
	int ret;

	if (!read_only) {
		ret = fttr_vlan_plan_build(&plan, fields[1], fields[2], fields[3], fields[4], fields[5]);
		if (ret)
			return ret;
	}
	ret = open_session(directory, id, interface, &session, &before);
	if (ret)
		return ret;
	memset(&change, 0, sizeof(change));
	if (read_only)
		ret = fttr_vlan_read(&session, fields[0], directory, &before, &change.before);
	else
		ret = fttr_vlan_apply(&session, fields[0], &plan, directory, &before, &change);
	close(session.fd);
	printf("{\"event\":\"%s\",\"id\":%u,\"instance\":%u,\"serial\":\"",
	       read_only ? "vlan_table" : "vlan_state", id, fields[0]);
	hex(before.serial, sizeof(before.serial));
	printf("\",\"error\":%d,\"before\":", ret);
	vlan_table_dump(&change.before);
	if (read_only) {
		printf(",\"last\":");
		vlan_failure_dump(&change.before.me);
	} else {
		printf(",\"phase\":\"%s\",\"sent\":%u,\"verified\":%s,\"restored\":%s,"
		       "\"cleanup_error\":%d,\"after\":", change.phase, change.sent,
		       change.verified ? "true" : "false", change.restored ? "true" : "false", change.cleanup_error);
		vlan_table_dump(&change.after);
		printf(",\"failure\":");
		vlan_failure_dump(&change.failure);
		printf(",\"cleanup_failure\":");
		vlan_failure_dump(&change.cleanup_failure);
	}
	puts("}");
	free(change.before.table.data);
	free(change.after.table.data);
	return ferror(stdout) ? -EIO : ret;
}

static int service_record(void *context, uint16_t sequence, const struct fttr_mib_record *entry)
{
	int ret;

	(void)sequence;
	ret = fttr_mib_model_add(context, entry);
	return ret < 0 ? ret : 0;
}

static int wifi_command(int directory, unsigned int id, const char *path, const char *interface)
{
	struct fttr_wifi_input input;
	struct fttr_wifi_job job = { 0 };
	struct fttr_mib_model model = { 0 };
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	uint16_t commands;
	int ret = fttr_wifi_config_read(path, &input);

	job.transaction.me.onu = id;
	if (ret)
		goto out;
	ret = open_session(directory, id, interface, &session, &before);
	if (ret)
		goto out;
	ret = fttr_mib_scan(&session, &commands, service_record, &model);
	if (!ret)
		ret = fttr_miniolt_same(directory, id, &before);
	if (!ret)
		ret = fttr_wifi_job_start(&job, id, &session.transaction, &model, &input.config, before.serial);
	if (!ret)
		ret = fttr_objects_execute(&session, &job.transaction, directory, &before);
	close(session.fd);
out:
	fttr_mib_model_clear(&model);
	if (ret && !job.transaction.error) {
		job.transaction.error = ret;
		job.transaction.phase = FTTR_OBJECT_FAILED;
	}
	{
		int result = fttr_wifi_job_dump(&job, stdout);

		return result ? result : ret;
	}
}

static int service_command(int directory, unsigned int id, const uint16_t fields[8],
			   bool tagged, const char *interface)
{
	struct fttr_service_config config = { .veip = fields[0] == 2, .shared = fields[0] >= 3, .multicast = fields[0] >= 4 ? fields[0] - 3 : 0, .uni = fields[1],
		.slot = fields[2], .priority = fields[3], .tagged = tagged, .vid = fields[4],
		.fixed = fields[5], .assured = fields[6], .maximum = fields[7] };
	struct fttr_service_job job = { .phase = "inventory" };
	struct fttr_mib_model model = { 0 };
	struct fttr_onu_state before;
	struct fttr_omci_session session;
	uint16_t commands;
	unsigned int i;
	int ret = fttr_miniolt_backend(directory);

	if (ret < 0)
		return ret;
	config.backend = ret;
	ret = fttr_miniolt_alloc_id(config.backend, id, config.slot);
	if (ret < 0)
		return ret;
	ret = open_session(directory, id, interface, &session, &before);
	if (ret)
		return ret;
	job.transaction.me.onu = id;
	memcpy(job.transaction.serial, before.serial, 8);
	ret = fttr_mib_scan(&session, &commands, service_record, &model);
	if (!ret)
		ret = fttr_miniolt_same(directory, id, &before);
	if (!ret)
		ret = fttr_service_start(&job, id, &session.transaction, &model, &config, before.serial);
	fttr_mib_model_clear(&model);
	if (!ret)
		ret = fttr_service_execute(&session, &job, directory, &before);
	close(session.fd);
	job.error = ret;
	fttr_service_dump(&job, stdout);
	if (job.transaction.count)
		fttr_objects_job_dump(&job.transaction, stdout, "service_objects");
	for (i = 0; i < job.uni_count; i++) {
		struct fttr_service_uni *uni = &job.unis[i];
		struct fttr_vlan_change *vlan = &uni->vlan;

		printf("{\"event\":\"service_uni\",\"kind\":%u,\"instance\":%u,\"port\":%u}\n",
			uni->veip ? 2 : 0, uni->instance, uni->port);
		fttr_objects_job_dump(&uni->transaction, stdout, "service_uni_objects");
		if (!vlan->before.me.request[2])
			continue;
		printf("{\"event\":\"service_vlan\",\"id\":%u,\"instance\":%u,\"before\":", id, uni->port);
		vlan_table_dump(&vlan->before);
		printf(",\"after\":");
		vlan_table_dump(&vlan->after);
		printf(",\"failure\":");
		vlan_failure_dump(&vlan->failure);
		printf(",\"cleanup_failure\":");
		vlan_failure_dump(&vlan->cleanup_failure);
		puts("}");
	}
	fttr_service_clear(&job);
	return ferror(stdout) ? -EIO : ret;
}

static int onu_admin(int directory, const char *id, const char *serial, const char *action,
		const char *target_id, const char *target_serial)
{
	struct fttr_admin_request request = { .version = 1 };
	struct sockaddr_un address = { .sun_family = AF_UNIX };
	struct timeval timeout = { .tv_sec = 30 };
	unsigned long number;
	unsigned int i;
	char *end;
	int fd, ret;
	int32_t result;
	ssize_t size;

	errno = 0;
	number = strtoul(id, &end, 10);
	if (errno || !*id || *end || number >= FTTR_ONUS || strlen(serial) != 16 ||
	    (strcmp(action, "block") && strcmp(action, "resume") && strcmp(action, "deactivate") && strcmp(action, "move")))
		return -EINVAL;
	request.id = number;
	request.action = !strcmp(action, "deactivate") ? FTTR_ADMIN_DEACTIVATE :
		!strcmp(action, "block") ? FTTR_ADMIN_BLOCK : FTTR_ADMIN_RESUME;
	for (i = 0; i < 8; i++) {
		char pair[3] = { serial[i * 2], serial[i * 2 + 1], 0 };

		if (strspn(pair, "0123456789abcdefABCDEF") != 2)
			return -EINVAL;
		request.serial[i] = strtoul(pair, NULL, 16);
	}
	if (!strcmp(action, "move")) {
		if (!target_id || !target_serial || strlen(target_serial) != 16)
			return -EINVAL;
		errno = 0;
		number = strtoul(target_id, &end, 10);
		if (errno || !*target_id || *end || number >= FTTR_ONUS || number == request.id)
			return -EINVAL;
		request.version = 2;
		request.action = FTTR_ADMIN_MOVE;
		request.target = number;
		for (i = 0; i < 8; i++) {
			char pair[3] = { target_serial[i * 2], target_serial[i * 2 + 1], 0 };

			if (strspn(pair, "0123456789abcdefABCDEF") != 2)
				return -EINVAL;
			request.target_serial[i] = strtoul(pair, NULL, 16);
		}
	}
	fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return -errno;
	/* Linux allocates a unique abstract return address for this client. */
	if (bind(fd, (struct sockaddr *)&address, sizeof(address.sun_family)) ||
	    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
	    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout))) {
		ret = -errno;
		goto out;
	}
	snprintf(address.sun_path, sizeof(address.sun_path), "/proc/self/fd/%d/control.sock", directory);
	if (connect(fd, (struct sockaddr *)&address, sizeof(address))) {
		ret = -errno;
		goto out;
	}
	unsigned int request_size = request.version == 1 ? offsetof(struct fttr_admin_request, target) : sizeof(request);
	size = send(fd, &request, request_size, 0);
	if (size != request_size) {
		ret = size < 0 ? -errno : -EIO;
		goto out;
	}
	size = recv(fd, &result, sizeof(result), MSG_TRUNC);
	ret = size < 0 ? -errno : size != sizeof(result) ? -EPROTO : result;
	if (!ret && request.action == FTTR_ADMIN_MOVE)
		printf("{\"event\":\"onu_move\",\"from\":%u,\"to\":%u,\"reservation_updated\":true,\"registration_verified\":false}\n",
			request.id, request.target);
	else if (!ret)
		printf("{\"event\":\"onu_admin\",\"id\":%u,\"serial\":\"%s\",\"blocked\":%s,\"temporary\":%s}\n",
			request.id, serial, request.action != FTTR_ADMIN_RESUME ? "true" : "false",
			request.action == FTTR_ADMIN_DEACTIVATE ? "true" : "false");
out:
	close(fd);
	return ret;
}

int main(int argc, char **argv)
{
	bool mib, wait, query, binding, gem, profiles, uni, ani, mapper, vlan, vlan_table, service, wifi;
	int directory, ret;

	if (argc < 3 || (strcmp(argv[2], "status") && strcmp(argv[2], "mib") &&
	    strcmp(argv[2], "wait-link") && strcmp(argv[2], "get") && strcmp(argv[2], "tcont") &&
	    strcmp(argv[2], "gem") && strcmp(argv[2], "profiles") && strcmp(argv[2], "uni") &&
	    strcmp(argv[2], "ani") && strcmp(argv[2], "mapper") && strcmp(argv[2], "vlan") &&
	    strcmp(argv[2], "vlan-table") && strcmp(argv[2], "service") && strcmp(argv[2], "wifi") &&
	    strcmp(argv[2], "onu-admin") && strcmp(argv[2], "onu-move") && strcmp(argv[2], "optical"))) {
		fprintf(stderr, "Usage: %s <miniolt-sysfs-directory> status\n"
			"       %s <miniolt-sysfs-directory> mib <hardware-ID> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> get <hardware-ID> <class> <instance> <mask> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> tcont <hardware-ID> <slot:1..7> <instance> <fixed> <assured> <maximum> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> gem <hardware-ID> <ordinal:1..60> <tcont-instance> <queue-instance> <association:0|1> <profile-instance> <gal-instance> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> profiles <hardware-ID> <bridge-instance> <per-UNI:0|1> <mapper-enabled:0|1> <mapper-instance> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> uni <hardware-ID> <kind:0=Ethernet,2=VEIP> <UNI-instance> <bridge-instance> <port-instance> <ordinal> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> ani <hardware-ID> <port-instance> <bridge-instance> <port-number> <mapper:0|1> <TP-instance> <VID-list|none> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> mapper <hardware-ID> <mapper-instance> <P-bit-mask> <GEM-IW-instance|0xffff> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> vlan-table <hardware-ID> <EVTO-instance> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> vlan <hardware-ID> <EVTO-instance> <policy:0..5> <input-VID> <output-VID> <priority:0..7> <remove:0|1> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> service <hardware-ID> <UNI-kind:0|2|shared|shared-mcast|shared-ctc> <UNI-instance:0-if-shared> <slot:1..7> <priority:0..7> <VID|none> <fixed> <assured> <maximum> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> wifi <hardware-ID> <config-file> [interface=fttr0]\n"
			"       %s <miniolt-sysfs-directory> wait-link <NI-sysfs-directory>\n",
			argv[0], argv[0], argv[0], argv[0], argv[0], argv[0], argv[0], argv[0], argv[0], argv[0], argv[0], argv[0], argv[0], argv[0]);
		fprintf(stderr, "       %s <controller-state-directory> onu-admin <hardware-ID> <serial-hex> <block|resume|deactivate>\n", argv[0]);
		fprintf(stderr, "       %s <controller-state-directory> onu-move <source-ID> <source-serial-hex> <target-ID> <target-serial-hex>\n", argv[0]);
		fprintf(stderr, "       %s <optical-sysfs-directory> optical\n", argv[0]);
		return 2;
	}
	if (!strcmp(argv[2], "optical")) {
		if (argc != 3)
			return 2;
		directory = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
		if (directory < 0) { perror(argv[1]); return 1; }
		ret = optical_status(directory);
		close(directory);
		if (!ret && fflush(stdout))
			ret = -errno;
		if (ret)
			fprintf(stderr, "FTTR optical: %s (%d)\n", strerror(-ret), ret);
		return ret ? 1 : 0;
	}
	if (!strcmp(argv[2], "onu-admin") || !strcmp(argv[2], "onu-move")) {
		bool move = !strcmp(argv[2], "onu-move");

		if (argc != (move ? 7 : 6))
			return 2;
		if (!move && !strcmp(argv[5], "move"))
			return 2;
		directory = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
		if (directory < 0) { perror(argv[1]); return 1; }
		ret = onu_admin(directory, argv[3], argv[4], move ? "move" : argv[5],
			move ? argv[5] : NULL, move ? argv[6] : NULL);
		close(directory);
		if (!ret && fflush(stdout))
			ret = -errno;
		if (ret)
			fprintf(stderr, "FTTR onu-admin: %s (%d)\n", strerror(-ret), ret);
		return ret ? 1 : 0;
	}
	mib = !strcmp(argv[2], "mib");
	wait = !strcmp(argv[2], "wait-link");
	query = !strcmp(argv[2], "get");
	binding = !strcmp(argv[2], "tcont");
	gem = !strcmp(argv[2], "gem");
	profiles = !strcmp(argv[2], "profiles");
	uni = !strcmp(argv[2], "uni");
	ani = !strcmp(argv[2], "ani");
	mapper = !strcmp(argv[2], "mapper");
	vlan = !strcmp(argv[2], "vlan");
	vlan_table = !strcmp(argv[2], "vlan-table");
	service = !strcmp(argv[2], "service");
	wifi = !strcmp(argv[2], "wifi");
	if ((mib && argc != 4 && argc != 5) || (query && argc != 7 && argc != 8) ||
	    (binding && argc != 9 && argc != 10) || (gem && argc != 10 && argc != 11) ||
	    (profiles && argc != 8 && argc != 9) || (uni && argc != 9 && argc != 10) ||
	    (ani && argc != 10 && argc != 11) || (mapper && argc != 7 && argc != 8) ||
	    (vlan && argc != 10 && argc != 11) || (vlan_table && argc != 5 && argc != 6) ||
	    (service && argc != 12 && argc != 13) ||
	    (wifi && argc != 5 && argc != 6) ||
	    (wait && argc != 4) || (!mib && !wait && !query && !binding && !gem && !profiles && !uni && !ani && !mapper && !vlan && !vlan_table && !service && !wifi && argc != 3))
		return 2;
	directory = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (directory < 0) {
		perror(argv[1]);
		return 1;
	}
	if (mib || query || binding || gem || profiles || uni || ani || mapper || vlan || vlan_table || service || wifi) {
		char *end;
		unsigned long id;

		errno = 0;
		id = strtoul(argv[3], &end, 10);
		if (errno || !*argv[3] || *end || id >= FTTR_ONUS) {
			close(directory);
			return 2;
		}
		if (wifi) {
			ret = wifi_command(directory, id, argv[4], argc == 6 ? argv[5] : "fttr0");
		} else if (query || binding || gem || profiles || uni || ani || mapper || vlan || vlan_table || service) {
			uint16_t fields[8];
			unsigned int i;

			for (i = 0; i < (service ? 8U : vlan_table ? 1U : (gem || vlan) ? 6U : profiles ? 4U : (binding || uni || ani) ? 5U : 3U); i++) {
				unsigned long value;

				if (service && !i && (!strcmp(argv[4], "shared") || !strcmp(argv[4], "shared-mcast") || !strcmp(argv[4], "shared-ctc"))) {
					fields[0] = !strcmp(argv[4], "shared") ? 3 : !strcmp(argv[4], "shared-mcast") ? 4 : 5;
					continue;
				}
				if (service && i == 4 && !strcmp(argv[8], "none")) {
					fields[4] = 0;
					continue;
				}
				errno = 0;
				value = strtoul(argv[4 + i], &end, 0);
				if (errno || !*argv[4 + i] || *end || value > UINT16_MAX ||
				    (query && i == 2 && !value) || (binding && i == 0 && (!value || value > 7)) ||
				    (gem && ((i == 0 && (!value || value > 60)) || (i == 3 && value > 1))) ||
				    (profiles && (i == 1 || i == 2) && value > 1) ||
				    (ani && ((i == 2 && (!value || value > 255)) || (i == 3 && value > 1))) ||
				    (mapper && i == 1 && (!value || value > 255)) ||
				    (vlan && ((i == 1 && value > 5) || (i == 4 && value > 7) || (i == 5 && value > 1))) ||
				    (service && ((i == 0 && value != 0 && value != 2) ||
				                 (i == 1 && fields[0] >= 3 && value) || (i == 2 && (!value || value > 7)) ||
				                 (i == 3 && value > 7) || (i == 4 && value > 4094))) ||
				    (uni && ((i == 0 && value != 0 && value != 2) ||
				             (i == 4 && value > (fields[0] == 2 ? 246U : 254U))))) {
					close(directory);
					return 2;
				}
				fields[i] = value;
			}
			if (service)
				ret = service_command(directory, id, fields, strcmp(argv[8], "none"), argc == 13 ? argv[12] : "fttr0");
			else if (vlan || vlan_table)
				ret = vlan_command(directory, id, fields, vlan_table,
					vlan_table ? (argc == 6 ? argv[5] : "fttr0") : (argc == 11 ? argv[10] : "fttr0"));
			else if (ani || mapper)
				ret = forwarding(directory, id, fields, mapper, ani ? argv[9] : NULL,
					ani ? (argc == 11 ? argv[10] : "fttr0") : (argc == 8 ? argv[7] : "fttr0"));
			else if (profiles || uni)
				ret = associate(directory, id, fields, profiles,
					profiles ? (argc == 9 ? argv[8] : "fttr0") : (argc == 10 ? argv[9] : "fttr0"));
			else if (gem)
				ret = create_gem(directory, id, fields, argc == 11 ? argv[10] : "fttr0");
			else if (binding)
				ret = bind_tcont(directory, id, fields, argc == 10 ? argv[9] : "fttr0");
			else
				ret = get(directory, id, fields, argc == 8 ? argv[7] : "fttr0");
		} else {
			ret = scan(directory, id, argc == 5 ? argv[4] : "fttr0");
		}
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
