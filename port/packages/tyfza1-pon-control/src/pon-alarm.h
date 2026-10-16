/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef TYFZA1_PON_ALARM_H
#define TYFZA1_PON_ALARM_H
#include "pon-oam.h"

#define PON_ALARM_OPTICAL_COUNT 20
#define PON_ALARM_PORT_COUNT 4
#define PON_ALARM_COUNT (PON_ALARM_OPTICAL_COUNT + PON_ALARM_PORT_COUNT)

struct pon_alarm_entry {
	struct pon_oam_alarm value;
	bool reported;
	bool sampled;
};

struct pon_alarm_table {
	struct pon_alarm_entry entries[PON_ALARM_COUNT];
	unsigned int generation;
};

void pon_alarm_init(struct pon_alarm_table *table);
int pon_alarm_access(struct pon_alarm_table *table, struct pon_oam_alarm *alarm,
	enum pon_oam_alarm_operation operation);
int pon_alarm_variable(struct pon_alarm_table *table, const struct pon_oam_request *request,
	const uint8_t *input, size_t size, uint8_t *output);
void pon_alarm_sample(struct pon_alarm_entry *entry, uint16_t raw);
size_t pon_alarm_pending(struct pon_alarm_table *table, unsigned int generation,
	struct pon_oam_alarm *events, size_t capacity);
void pon_alarm_reported(struct pon_alarm_table *table, const struct pon_oam_alarm *events, size_t count);
#endif
