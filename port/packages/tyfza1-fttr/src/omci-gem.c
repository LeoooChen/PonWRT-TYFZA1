// SPDX-License-Identifier: GPL-2.0-only
#include <string.h>

#include "omci-gem.h"

static void be16(uint8_t *p, uint16_t value)
{
	p[0] = value >> 8;
	p[1] = value;
}

static void be32(uint8_t *p, uint32_t value)
{
	p[0] = value >> 24;
	p[1] = value >> 16;
	p[2] = value >> 8;
	p[3] = value;
}

void fttr_gem_job_start(struct fttr_gem_job *job, uint16_t onu, uint16_t *counter,
			const struct fttr_gem_config *config, const uint8_t serial[8])
{
	struct fttr_omci_object *object;
	int count = 0;

	memset(job, 0, sizeof(*job));
	if (config->descriptor) {
		object = &job->objects[count++];
		object->class_id = 280;
		object->instance = config->descriptor;
		object->create.mask = 0xff00;
		object->create.size = 20;
		be32(object->create.value, config->committed);
		be32(object->create.value + 4, config->peak);
	}
	object = &job->objects[count++];
	object->class_id = 268;
	object->instance = config->entity;
	object->create.mask = 0xfac0;
	object->create.size = 14;
	be16(object->create.value, config->entity);
	be16(object->create.value + 2, config->tcont);
	object->create.value[4] = 3;
	be16(object->create.value + 5, config->queue);
	be16(object->create.value + 7, config->descriptor);
	object = &job->objects[count++];
	object->class_id = 266;
	object->instance = config->entity;
	object->create.mask = 0xf200;
	object->create.size = 9;
	be16(object->create.value, config->entity);
	object->create.value[2] = config->bridge ? 1 : 5;
	be16(object->create.value + 3, config->profile);
	be16(object->create.value + 7, config->gal);
	job->objects[count - 1].dependencies = 1U << (count - 2);
	if (config->descriptor)
		job->objects[1].dependencies = 1;
	fttr_objects_job_start(&job->transaction, onu, counter, job->objects, count, serial);
}
