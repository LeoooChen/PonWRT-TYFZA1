// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "omci-model.h"

struct fttr_mib_schema {
	uint16_t class_id;
	uint8_t width[16];
};

/* Wire widths from the original plr_gponomci_medetail_get descriptors.
 * Original RAM widths/endian conversions are not the wire representation.
 * Class 53 has two descriptors for mask 0400; keep it opaque until resolved.
 */
static const struct fttr_mib_schema schemas[] = {
	{ 2, { 1 } },
	{ 5, { 1, 1, 1, 20, 20, 1, 1, 1, 1 } },
	{ 6, { 1, 1, 8, 14, 4, 1, 1, 1, 20, 1, 1, 1, 1, 4 } },
	{ 7, { 14, 1, 1, 1, 25, 16 } },
	{ 11, { 1, 1, 1, 1, 1, 1, 1, 2, 1, 2, 1, 1, 1, 1, 1 } },
	{ 24, { 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4 } },
	{ 45, { 1, 1, 1, 2, 2, 2, 2, 1, 1, 4 } },
	{ 46, { 6, 2, 8, 4, 1, 2, 2, 2 } },
	{ 47, { 2, 1, 1, 2, 2, 2, 1, 1, 1, 6, 2, 2, 1 } },
	{ 49, { 8 } },
	{ 50, { 8 } },
	{ 51, { 1, 2, 4 } },
	{ 52, { 1, 2, 4, 4, 4, 4, 4 } },
	{ 82, { 1, 1, 1, 1, 1 } },
	{ 84, { 24, 1, 1 } },
	{ 89, { 1, 2, 4 } },
	{ 130, { 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 24, 1, 1 } },
	{ 131, { 4, 20, 14, 14 } },
	{ 134, { 1, 6, 25, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 25, 25 } },
	{ 136, { 2, 1, 1, 2 } },
	{ 137, { 2, 2 } },
	{ 138, { 1, 1, 4, 2, 1, 1, 25 } },
	{ 148, { 1, 25, 25, 25 } },
	{ 150, { 2, 2, 4, 4, 2, 4, 4, 2, 1, 2, 4, 5, 1, 1, 2 } },
	{ 153, { 2, 2, 25, 2, 2, 4, 2, 2, 2, 2, 1, 1 } },
	{ 157, { 1, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25 } },
	{ 171, { 1, 2, 2, 2, 1, 16, 2, 24 } },
	{ 256, { 4, 14, 8, 1, 1, 1, 1, 1, 1, 24, 12, 1, 2 } },
	{ 257, { 20, 1, 2, 1, 1, 2, 1, 1, 2, 4, 2, 1, 2, 2 } },
	{ 262, { 2, 1, 1 } },
	{ 263, { 1, 2, 2, 1, 1, 1, 1, 1, 1, 2, 1, 1, 2, 2, 1, 1 } },
	{ 264, { 2, 1, 1, 2, 2 } },
	{ 266, { 2, 1, 2, 2, 1, 1, 2, 1 } },
	{ 268, { 2, 2, 1, 2, 2, 1, 2, 1, 2, 1 } },
	{ 272, { 2 } },
	{ 273, { 4, 4, 4, 4, 4, 4, 4 } },
	{ 274, { 4, 4, 4, 4, 4, 4, 4 } },
	{ 277, { 1, 2, 2, 2, 2, 4, 2, 1, 2, 4, 2, 2, 8, 2, 1, 1 } },
	{ 278, { 2, 2, 1, 1 } },
	{ 280, { 4, 4, 4, 4, 1, 1, 1, 1 } },
	{ 281, { 2, 1, 2, 2, 1, 1, 2, 1, 12, 24 } },
	{ 296, { 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4 } },
	{ 309, { 1, 1, 1, 2, 1, 4, 24, 24, 10, 1, 4, 4, 4, 4, 1, 3 } },
	{ 310, { 1, 2, 2, 4, 1, 20, 22 } },
	{ 311, { 1, 4, 4, 4, 24 } },
	{ 329, { 1, 1, 25, 2, 2 } },
	{ 340, { 1, 2, 2 } },
	{ 384, { 30, 1, 1, 1 } },
	{ 65380, { 1, 1, 2, 1, 1 } },
	{ 65381, { 1, 24, 24, 1, 1, 1, 4, 4, 4, 4, 4, 1, 1, 24 } },
	{ 65385, { 1, 1, 24, 1, 1, 2, 2, 2, 1, 1, 1, 1 } },
	{ 65386, { 1, 24, 1, 1, 1, 26, 26, 26, 26, 1 } },
	{ 65392, { 1 } },
	{ 65528, { 4, 2, 2 } },
	{ 65529, { 4, 1, 1, 1 } },
	{ 65530, { 4, 24, 12, 1 } },
	{ 65531, { 1, 1, 1, 2, 1, 4, 30, 30, 16, 1, 16, 4, 4, 4, 1, 3 } },
};

int fttr_mib_model_add(struct fttr_mib_model *model, const struct fttr_mib_record *record)
{
	const struct fttr_mib_schema *schema = NULL;
	struct fttr_mib_entity **position = &model->entities, *entity;
	size_t total = 0, wire = 0, offset = 0, i;
	uint16_t supported = 0;

	for (i = 0; i < sizeof(schemas) / sizeof(schemas[0]); i++)
		if (schemas[i].class_id == record->class_id) {
			schema = &schemas[i];
			break;
		}
	if (schema)
		for (i = 0; i < 16; i++) {
			total += schema->width[i];
			if (schema->width[i])
				supported |= 0x8000U >> i;
			if (record->mask & (0x8000U >> i))
				wire += schema->width[i];
		}
	if (wire > sizeof(record->attributes))
		return -EMSGSIZE;
	while (*position && ((*position)->class_id < record->class_id ||
	       ((*position)->class_id == record->class_id && (*position)->instance < record->instance)))
		position = &(*position)->next;
	entity = *position;
	if (!entity || entity->class_id != record->class_id || entity->instance != record->instance) {
		entity = calloc(1, sizeof(*entity) + total);
		if (!entity)
			return -ENOMEM;
		entity->schema = schema;
		entity->class_id = record->class_id;
		entity->instance = record->instance;
		entity->next = *position;
		*position = entity;
		model->count++;
	}
	entity->reported |= record->mask;
	if (!schema || (record->mask & ~supported)) {
		model->unparsed_records++;
		return 1;
	}
	wire = 0;
	for (i = 0; i < 16; i++) {
		if (record->mask & (0x8000U >> i)) {
			memcpy(entity->data + offset, record->attributes + wire, schema->width[i]);
			wire += schema->width[i];
		}
		offset += schema->width[i];
	}
	entity->present |= record->mask;
	return 0;
}

const uint8_t *fttr_mib_entity_attr(const struct fttr_mib_entity *entity, uint16_t mask, size_t *size)
{
	size_t offset = 0, i;

	if (!(entity->present & mask))
		return NULL;
	for (i = 0; i < 16; i++) {
		if (mask == (0x8000U >> i)) {
			*size = entity->schema->width[i];
			return entity->data + offset;
		}
		offset += entity->schema->width[i];
	}
	return NULL;
}

int fttr_mib_model_dump(const struct fttr_mib_model *model, FILE *file)
{
	const struct fttr_mib_entity *entity;
	size_t i, offset, n;
	bool first;

	for (entity = model->entities; entity; entity = entity->next) {
		fprintf(file, "{\"event\":\"mib_entity\",\"class\":%u,\"instance\":%u,"
			"\"reported_mask\":%u,\"present_mask\":%u,\"attributes\":[",
			entity->class_id, entity->instance, entity->reported, entity->present);
		first = true;
		offset = 0;
		for (i = 0; entity->schema && i < 16; i++) {
			if (entity->present & (0x8000U >> i)) {
				fprintf(file, "%s{\"mask\":%u,\"value\":\"", first ? "" : ",", 0x8000U >> i);
				for (n = 0; n < entity->schema->width[i]; n++)
					fprintf(file, "%02x", entity->data[offset + n]);
				fputs("\"}", file);
				first = false;
			}
			offset += entity->schema->width[i];
		}
		fputs("]}\n", file);
	}
	fprintf(file, "{\"event\":\"mib_model\",\"entities\":%u,\"unparsed_records\":%u}\n",
		model->count, model->unparsed_records);
	return ferror(file) ? -EIO : 0;
}

void fttr_mib_model_clear(struct fttr_mib_model *model)
{
	struct fttr_mib_entity *entity;

	while ((entity = model->entities)) {
		model->entities = entity->next;
		free(entity);
	}
	model->count = 0;
	model->unparsed_records = 0;
}
