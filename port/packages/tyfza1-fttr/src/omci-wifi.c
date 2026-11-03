// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>

#include "omci-wifi.h"

static struct fttr_omci_values *group(struct fttr_wifi_job *job, uint16_t class_id,
				    uint16_t instance, uint16_t mask, uint8_t size)
{
	struct fttr_omci_object *object = &job->objects[job->transaction.count++];

	object->class_id = class_id;
	object->instance = instance;
	object->update_only = true;
	object->create.mask = mask;
	object->create.size = size;
	return &object->create;
}

int fttr_wifi_job_start(struct fttr_wifi_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_mib_model *model, const struct fttr_wifi_config *config,
		const uint8_t serial[8])
{
	const struct fttr_mib_entity *entity;
	struct fttr_omci_values *values;
	bool config_present = false, security_present = false;
	bool indexed_keys = config->security == 5 || config->security == 6;
	bool bandwidth = config->mode == 3 || config->mode == 4 || config->mode == 6 || config->mode == 7;
	int i, count, ret = 0;

	memset(job, 0, sizeof(*job));
	job->transaction.me.onu = onu;
	memcpy(job->transaction.serial, serial, 8);
	if (config->ssid_size > 24 || (!indexed_keys && config->security && config->shared_key_size > 24))
		ret = -EMSGSIZE;
	if (indexed_keys)
		for (i = 0; i < 4; i++)
			if (config->key_size[i] > 26)
				ret = -EMSGSIZE;
	for (entity = model->entities; entity; entity = entity->next) {
		if (entity->instance != config->instance)
			continue;
		config_present |= entity->class_id == 0xff69;
		security_present |= entity->class_id == 0xff6a;
	}
	if (!ret && (!config_present || !security_present))
		ret = -EOPNOTSUPP;
	if (ret) {
		job->transaction.phase = FTTR_OBJECT_FAILED;
		job->transaction.error = ret;
		return ret;
	}
	values = group(job, 0xff69, config->instance, bandwidth ? 0xd000 : 0xc000, bandwidth ? 3 : 2);
	values->value[0] = config->mode;
	values->value[1] = config->channel;
	if (bandwidth)
		values->value[2] = config->bandwidth;
	values = group(job, 0xff69, config->instance, 0x2000, 24);
	if (config->ssid_size)
		memcpy(values->value, config->ssid, config->ssid_size);
	if (!config->security) {
		group(job, 0xff6a, config->instance, 0x8000, 1);
	} else if (indexed_keys) {
		values = group(job, 0xff6a, config->instance, 0xa800, 3);
		values->value[0] = config->security;
		values->value[1] = config->key_auth;
		values->value[2] = config->key_index;
		for (i = 0; i < 4; i++) {
			values = group(job, 0xff6a, config->instance, 0x0400 >> i, 26);
			if (config->key_size[i])
				memcpy(values->value, config->keys[i], config->key_size[i]);
		}
	} else {
		values = group(job, 0xff6a, config->instance, 0x8040, 2);
		values->value[0] = config->security;
		values->value[1] = config->encryption;
		values = group(job, 0xff6a, config->instance, 0x4000, 24);
		if (config->shared_key_size)
			memcpy(values->value, config->shared_key, config->shared_key_size);
	}
	values = group(job, 0xff69, config->instance, 0x0020, 1);
	values->value[0] = config->ssid_enabled;
	values = group(job, 0xff69, config->instance, 0x0010, 1);
	values->value[0] = config->radio_enabled;
	count = job->transaction.count;
	fttr_objects_job_start(&job->transaction, onu, counter, job->objects, count, serial);
	return 0;
}

void fttr_wifi_job_cancel(struct fttr_wifi_job *job, int error)
{
	struct fttr_objects_job *transaction = &job->transaction;

	if (transaction->phase == FTTR_OBJECT_FAILED)
		return;
	if (transaction->error) {
		transaction->cleanup_error = error;
		transaction->cleanup_failure = transaction->me;
	} else {
		transaction->error = error;
		transaction->failure = transaction->me;
	}
	transaction->phase = FTTR_OBJECT_FAILED;
}

int fttr_wifi_job_dump(const struct fttr_wifi_job *job, FILE *file)
{
	const struct fttr_objects_job *transaction = &job->transaction;
	int i;

	fprintf(file, "{\"event\":\"wifi_state\",\"id\":%u,\"configured\":%s,\"error\":%d,"
		"\"cleanup_error\":%d,\"groups\":[", transaction->me.onu,
		transaction->phase == FTTR_OBJECT_CONFIGURED ? "true" : "false",
		transaction->error, transaction->cleanup_error);
	for (i = 0; i < transaction->count; i++) {
		const struct fttr_omci_object *object = &job->objects[i];

		fprintf(file, "%s{\"class\":%u,\"instance\":%u,\"mask\":%u,\"state\":%u,\"touched\":%s}",
			i ? "," : "", object->class_id, object->instance, object->create.mask,
			object->presence, object->touched ? "true" : "false");
	}
	fprintf(file, "],\"failure_result\":%u,\"failure_replied\":%s,\"cleanup_result\":%u,\"cleanup_replied\":%s}\n",
		transaction->failure.result, transaction->failure.replied ? "true" : "false",
		transaction->cleanup_failure.result, transaction->cleanup_failure.replied ? "true" : "false");
	return ferror(file) ? -EIO : 0;
}

/* Byte strings are hex to preserve whitespace and full-width fields. */
int fttr_wifi_config_read(const char *path, struct fttr_wifi_input *input)
{
	static const char * const names[] = { "instance", "mode", "channel", "bandwidth",
		"security", "encryption", "key_auth", "key_index", "ssid_enabled", "radio_enabled",
		"ssid_hex", "shared_key_hex", "key1_hex", "key2_hex", "key3_hex", "key4_hex" };
	struct fttr_wifi_config *config = &input->config;
	unsigned long number[10] = { 0 };
	size_t sizes[6] = { 0 }, capacity = 0;
	unsigned int seen = 0, required = 0x7ff;
	char *line = NULL;
	ssize_t length;
	FILE *file = fopen(path, "r");
	int ret = 0;

	if (!file)
		return -errno;
	memset(input, 0, sizeof(*input));
	while ((length = getline(&line, &capacity, file)) >= 0) {
		char *key, *value, *extra, *save, *end;
		unsigned int i;

		/* Embedded NULs must not hide a suffix from validation. */
		if (memchr(line, '\0', length)) {
			ret = -EINVAL;
			break;
		}
		key = strtok_r(line, " \t\r\n", &save);
		if (!key || *key == '#')
			continue;
		value = strtok_r(NULL, " \t\r\n", &save);
		extra = strtok_r(NULL, " \t\r\n", &save);
		if (!value || (extra && *extra != '#')) { ret = -EINVAL; break; }
		for (i = 0; i < sizeof(names) / sizeof(names[0]); i++)
			if (!strcmp(key, names[i]))
				break;
		if (i == sizeof(names) / sizeof(names[0]) || (seen & (1U << i))) {
			ret = -EINVAL;
			break;
		}
		seen |= 1U << i;
		if (i < 10) {
			errno = 0;
			number[i] = strtoul(value, &end, 0);
			if (errno || *end || *value == '-' ||
			    number[i] > (i == 0 ? UINT16_MAX : i >= 8 ? 1U : UINT8_MAX)) {
				ret = -EINVAL;
				break;
			}
		} else {
			size_t n, length = !strcmp(value, "-") ? 0 : strlen(value);

			if (length % 2) { ret = -EINVAL; break; }
			if (length / 2 > (i < 12 ? 24U : 26U)) { ret = -EMSGSIZE; break; }
			for (n = 0; n < length; n += 2) {
				char byte[3] = { value[n], value[n + 1], 0 };

				if (!isxdigit((unsigned char)byte[0]) || !isxdigit((unsigned char)byte[1])) {
					ret = -EINVAL;
					break;
				}
				input->bytes[i - 10][n / 2] = strtoul(byte, NULL, 16);
			}
			if (ret)
				break;
			sizes[i - 10] = length / 2;
		}
	}
	if (!ret && ferror(file))
		ret = -EIO;
	free(line);
	fclose(file);
	if (number[4] == 5 || number[4] == 6)
		required |= 0xf000;
	else if (number[4])
		required |= 0x0800;
	if (!ret && (seen & required) != required)
		ret = -EINVAL;
	if (ret)
		return ret;
	*config = (struct fttr_wifi_config) {
		.instance = number[0], .mode = number[1], .channel = number[2], .bandwidth = number[3],
		.security = number[4], .encryption = number[5], .key_auth = number[6], .key_index = number[7],
		.ssid_enabled = number[8], .radio_enabled = number[9],
		.ssid = input->bytes[0], .ssid_size = sizes[0],
		.shared_key = input->bytes[1], .shared_key_size = sizes[1],
		.keys = { input->bytes[2], input->bytes[3], input->bytes[4], input->bytes[5] },
		.key_size = { sizes[2], sizes[3], sizes[4], sizes[5] }
	};
	return 0;
}
