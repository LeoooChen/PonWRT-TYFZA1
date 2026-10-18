// SPDX-License-Identifier: GPL-2.0-only
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
