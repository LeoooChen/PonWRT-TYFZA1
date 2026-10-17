/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_MODEL_H
#define TYFZA1_FTTR_OMCI_MODEL_H

#include <stdio.h>
#include "omci-mib.h"

struct fttr_mib_schema;

struct fttr_mib_entity {
	struct fttr_mib_entity *next;
	const struct fttr_mib_schema *schema;
	uint16_t class_id, instance, reported, present;
	uint8_t data[];
};

struct fttr_mib_model {
	struct fttr_mib_entity *entities;
	unsigned int count, unparsed_records;
};

/* A positive result preserves an entity whose wire layout is not known. */
int fttr_mib_model_add(struct fttr_mib_model *model, const struct fttr_mib_record *record);
const uint8_t *fttr_mib_entity_attr(const struct fttr_mib_entity *entity, uint16_t mask, size_t *size);
int fttr_mib_model_dump(const struct fttr_mib_model *model, FILE *file);
void fttr_mib_model_clear(struct fttr_mib_model *model);

#endif
