// SPDX-License-Identifier: GPL-2.0-or-later
/* CTC alarm state and hysteresis, from the board's eponoamd alarm callbacks. */
#include <errno.h>
#include <string.h>
#include "pon-alarm.h"

static uint16_t get16(const uint8_t *value)
{
	return (uint16_t)value[0] << 8 | value[1];
}

static uint32_t get32(const uint8_t *value)
{
	return (uint32_t)value[0] << 24 | (uint32_t)value[1] << 16 |
		(uint32_t)value[2] << 8 | value[3];
}

static void put32(uint8_t *value, uint32_t number)
{
	value[0] = number >> 24;
	value[1] = number >> 16;
	value[2] = number >> 8;
	value[3] = number;
}

void pon_alarm_init(struct pon_alarm_table *table)
{
	/* Exact selectors in stock0x412058/0x412224 and totals in0x411e9c.
	 * Drop/discard/fragment/jabber IDs without an established physical
	 * counter are deliberately absent, rather than backed by DAL zeros. */
	static const uint16_t counter_ids[] = {0x117, 0x119, 0x11b, 0x11a, 0x11c, 0x123, 0x124};
	unsigned int i;

	memset(table, 0, sizeof(*table));
	for (i = 0; i < PON_ALARM_OPTICAL_COUNT; i++) {
		struct pon_oam_alarm *alarm = &table->entries[i].value;

		alarm->object_type = 4;
		alarm->id = 0x101 + i;
		alarm->info_length = 4;
		/* Actual stock table0x47b0f8: alarms and warnings use raw SFF words. */
		alarm->threshold = i & 1 ? 0 : i % 10 == 8 ? 0x7fff : UINT32_MAX;
		alarm->clear_threshold = alarm->threshold;
	}
	for (i = 0; i < PON_ALARM_PORT_COUNT; i++) {
		struct pon_oam_alarm *alarm = &table->entries[PON_ALARM_OPTICAL_COUNT + i].value;

		alarm->object_type = 1;
		alarm->instance = 0x01000001 + i;
		alarm->id = 0x302; /* UNI loss of signal. */
	}
	for (i = 0; i < PON_ALARM_PON_COUNTER_COUNT; i++) {
		struct pon_oam_alarm *alarm = &table->entries[PON_ALARM_PON_COUNTER_BASE + i].value;

		alarm->object_type = 4;
		alarm->id = counter_ids[i % 7] + (i >= 7 ? 0x10 : 0);
		alarm->info_length = 8;
		alarm->threshold = alarm->clear_threshold = UINT32_MAX;
	}
	for (i = 0; i < PON_ALARM_PORT_COUNT * 2; i++) {
		struct pon_oam_alarm *alarm = &table->entries[PON_ALARM_LINK_COUNTER_BASE + i].value;

		alarm->object_type = 1;
		alarm->instance = 0x01000001 + i / 2;
		alarm->id = i & 1 ? 0x327 : 0x316;
		alarm->info_length = 8;
		alarm->threshold = alarm->clear_threshold = UINT32_MAX;
	}
}

static struct pon_alarm_entry *find(struct pon_alarm_table *table, const struct pon_oam_alarm *alarm)
{
	unsigned int i;

	for (i = 0; i < PON_ALARM_COUNT; i++) {
		struct pon_alarm_entry *entry = &table->entries[i];

		if (entry->value.id == alarm->id && entry->value.object_type == alarm->object_type &&
		    (alarm->object_type != 1 || (uint16_t)entry->value.instance == (uint16_t)alarm->instance))
			return entry;
	}
	return NULL;
}

int pon_alarm_access(struct pon_alarm_table *table, struct pon_oam_alarm *alarm,
	enum pon_oam_alarm_operation operation)
{
	struct pon_alarm_entry *entry = find(table, alarm);

	if (!entry)
		return -EOPNOTSUPP;
	switch (operation) {
	case PON_ALARM_STATUS_SET:
		if (alarm->enabled > 1)
			return -EINVAL;
		if (entry->value.enabled != alarm->enabled) {
			entry->value.active = false;
			entry->reported = false;
		}
		entry->value.enabled = alarm->enabled;
		entry->value.instance = alarm->instance;
		break;
	case PON_ALARM_THRESHOLD_SET:
		if (!entry->value.info_length)
			return -EOPNOTSUPP;
		entry->value.threshold = alarm->threshold;
		entry->value.clear_threshold = alarm->clear_threshold;
		break;
	case PON_ALARM_THRESHOLD_GET:
		if (!entry->value.info_length)
			return -EOPNOTSUPP;
		break;
	case PON_ALARM_STATUS_GET:
		break;
	}
	*alarm = entry->value;
	return 0;
}

int pon_alarm_variable(struct pon_alarm_table *table, const struct pon_oam_request *request,
	const uint8_t *input, size_t size, uint8_t *output)
{
	struct pon_oam_alarm alarm = {.object_type = 4};
	enum pon_oam_alarm_operation operation;
	const uint8_t *object = request->object;
	int ret;

	if (request->leaf == 0x81) {
		if (!input || size != 6 || (get32(input + 2) != 1 && get32(input + 2) != 2))
			return -EINVAL;
		operation = PON_ALARM_STATUS_SET;
		alarm.enabled = get32(input + 2) == 2;
	} else if (input) {
		if (size != 10)
			return -EINVAL;
		operation = PON_ALARM_THRESHOLD_SET;
		alarm.threshold = get32(input + 2);
		alarm.clear_threshold = get32(input + 6);
	} else {
		if (request->parameter_size != 2)
			return -EINVAL;
		operation = PON_ALARM_THRESHOLD_GET;
		input = request->parameters;
	}
	alarm.id = get16(input);
	if (object && get16(object + 1) == 1) {
		if (request->object_size == 5 && ((object[4] > 0 && object[4] < 0x50) || object[4] == 0xff)) {
			alarm.object_type = 1;
			alarm.instance = object[4] == 0xff ? 0x0100ffff : 0x01000000 | object[4];
		} else if (request->object_size == 8 && object[4] == 1) {
			alarm.object_type = 1;
			alarm.instance = get32(object + 4);
		}
	}
	if (alarm.object_type == 1 && (uint16_t)alarm.instance == 0xffff && input &&
	    operation != PON_ALARM_THRESHOLD_GET) {
		unsigned int port;

		for (port = 1; port <= PON_ALARM_PORT_COUNT; port++) {
			struct pon_oam_alarm selected = alarm;

			selected.instance = (alarm.instance & 0xffff0000) | port;
			ret = pon_alarm_access(table, &selected, operation);
			if (ret)
				return ret;
		}
		return 0;
	}
	ret = pon_alarm_access(table, &alarm, operation);
	if (ret || operation != PON_ALARM_THRESHOLD_GET)
		return ret;
	output[0] = alarm.id >> 8;
	output[1] = alarm.id;
	put32(output + 2, alarm.threshold);
	/* Stock0x424ef8 mistakenly copies threshold twice. Return the real clear value. */
	put32(output + 6, alarm.clear_threshold);
	return 10;
}

void pon_alarm_sample(struct pon_alarm_entry *entry, uint16_t raw)
{
	struct pon_oam_alarm *alarm = &entry->value;
	bool active;

	if (!alarm->enabled)
		return;
	entry->sampled = true;
	if (alarm->object_type == 1) {
		active = !raw;
	} else {
		bool high = alarm->id & 1;
		bool temperature = (alarm->id - 0x101) % 10 >= 8;
		int64_t current = temperature ? (int16_t)raw : raw;
		uint32_t limit = alarm->active ? alarm->clear_threshold : alarm->threshold;
		int64_t threshold = temperature ? (int64_t)(int16_t)limit : (int64_t)limit;

		/* Power's log10 and voltage/bias scaling are monotonic; compare the
		 * exact raw values. Equality retains the previous hysteresis state. */
		active = alarm->active ? !(high ? current < threshold : current > threshold) :
			(high ? current > threshold : current < threshold);
	}
	if (active != alarm->active && alarm->info_length)
		put32(alarm->info, raw);
	alarm->active = active;
}

void pon_alarm_counter_sample(struct pon_alarm_entry *entry, uint64_t counter)
{
	struct pon_oam_alarm *alarm = &entry->value;
	bool active;

	if (!alarm->enabled)
		return;
	entry->sampled = true;
	/* Stock0x42b1b4: cumulative hardware value, not a PM interval delta.
	 * Equality retains the previous state on both sides of the hysteresis. */
	active = alarm->active ? counter >= alarm->clear_threshold : counter > alarm->threshold;
	if (active != alarm->active) {
		put32(alarm->info, counter >> 32);
		put32(alarm->info + 4, counter);
	}
	alarm->active = active;
}

void pon_alarm_pm_sample(struct pon_alarm_table *table, const struct pon_pm_port *port)
{
	static const unsigned int fields[] = {10, 12, 14, 13, 15};
	uint64_t values[7];
	unsigned int i;

	/* PM's baseline plus current reconstructs the sampled lifetime value,
	 * including immediately after an OLT reset or a period rollover. */
	for (i = 0; i < 5; i++)
		values[i] = port->baseline[fields[i]] + port->current[fields[i]];
	values[5] = values[0] + values[1] + values[2];
	values[6] = values[3] + values[4];
	for (i = 0; i < PON_ALARM_PON_COUNTER_COUNT; i++)
		pon_alarm_counter_sample(&table->entries[PON_ALARM_PON_COUNTER_BASE + i], values[i % 7]);
}

void pon_alarm_link_sample(struct pon_alarm_table *table, unsigned int port, uint64_t changes)
{
	for (unsigned int i = 0; i < 2; i++)
		pon_alarm_counter_sample(&table->entries[PON_ALARM_LINK_COUNTER_BASE + port * 2 + i], changes);
}

size_t pon_alarm_pending(struct pon_alarm_table *table, unsigned int generation,
	struct pon_oam_alarm *events, size_t capacity)
{
	size_t count = 0;
	unsigned int i;

	if (table->generation != generation) {
		for (i = 0; i < PON_ALARM_COUNT; i++)
			table->entries[i].reported = false;
		table->generation = generation;
	}
	for (i = 0; i < PON_ALARM_COUNT && count < capacity; i++) {
		struct pon_alarm_entry *entry = &table->entries[i];

		if (entry->sampled && entry->value.enabled && entry->value.active != entry->reported)
			events[count++] = entry->value;
	}
	return count;
}

void pon_alarm_reported(struct pon_alarm_table *table, const struct pon_oam_alarm *events, size_t count)
{
	for (size_t i = 0; i < count; i++) {
		struct pon_alarm_entry *entry = find(table, &events[i]);

		entry->reported = events[i].active;
	}
}
