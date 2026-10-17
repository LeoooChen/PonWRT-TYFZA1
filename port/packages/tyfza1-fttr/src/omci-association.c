// SPDX-License-Identifier: GPL-2.0-only
#include <string.h>

#include "omci-association.h"

static void be16(uint8_t *p, uint16_t value)
{
	p[0] = value >> 8;
	p[1] = value;
}

void fttr_profiles_job_start(struct fttr_association_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_profiles_config *config, const uint8_t serial[8])
{
	struct fttr_omci_object *object;
	int count = 2;

	memset(job, 0, sizeof(*job));
	object = &job->objects[0];
	object->class_id = 272;
	object->instance = 1;
	object->create.mask = 0x8000;
	object->create.size = 2;
	be16(object->create.value, 1600);
	object = &job->objects[1];
	object->class_id = 45;
	object->instance = config->bridge;
	object->create.mask = config->per_uni ? 0xffc0 : 0xff80;
	object->create.size = config->per_uni ? 17 : 13;
	object->create.value[0] = !config->per_uni;
	object->create.value[1] = 1;
	object->create.value[2] = !config->per_uni;
	be16(object->create.value + 3, config->per_uni ? 0x8000 : 0xffff);
	be16(object->create.value + 5, config->per_uni ? 0x1400 : 0x0f00);
	be16(object->create.value + 7, config->per_uni ? 0x0200 : 0x0500);
	be16(object->create.value + 9, 0x0f00);
	if (config->use_mapper) {
		object = &job->objects[count++];
		object->class_id = 130;
		object->instance = config->mapper;
		object->create.mask = 0xffc0;
		object->create.size = 19;
		memset(object->create.value, 0xff, 18);
		object->create.value[18] = 1;
	}
	fttr_objects_job_start(&job->transaction, onu, counter, job->objects, count, serial);
}

void fttr_uni_job_start(struct fttr_association_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_uni_config *config, const uint8_t serial[8])
{
	struct fttr_omci_object *object;
	int count = 1;

	memset(job, 0, sizeof(*job));
	object = &job->objects[0];
	object->class_id = 47;
	object->instance = config->entity;
	object->create.mask = config->veip ? 0xfc00 : 0xfe00;
	object->create.size = config->veip ? 10 : 11;
	be16(object->create.value, config->bridge);
	object->create.value[2] = config->ordinal + (config->veip ? 9 : 1);
	object->create.value[3] = config->veip ? 11 : 1;
	be16(object->create.value + 4, config->uni);
	be16(object->create.value + 8, 1);
	if (!config->veip) {
		object->create.value[10] = 1;
		object = &job->objects[count++];
		object->class_id = 84;
		object->instance = config->entity;
		object->create.mask = 0x4000;
		object->create.size = 1;
		object->dependencies = 1;
	}
	object = &job->objects[count++];
	object->class_id = 171;
	object->instance = config->entity;
	object->create.mask = 0x8200;
	object->create.size = 3;
	object->create.value[0] = config->veip ? 10 : 2;
	be16(object->create.value + 1, config->uni);
	object->set.mask = 0x3100;
	object->set.size = 28;
	be16(object->set.value, 0x8100);
	be16(object->set.value + 2, 0x8100);
	/* The original 24-byte default DSCP map at 133d08 contains zeros. */
	object->dependencies = 1;
	fttr_objects_job_start(&job->transaction, onu, counter, job->objects, count, serial);
}
