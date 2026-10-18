/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef TYFZA1_PON_PM_H
#define TYFZA1_PON_PM_H
#include "pon-oam.h"

#define PON_PM_PORTS 5
#define PON_PM_COUNTERS 37
#define PON_PM_RECORD_BYTES (8 + PON_PM_COUNTERS * 8)

struct pon_pm_port {
	uint64_t baseline[PON_PM_COUNTERS];
	uint64_t current[PON_PM_COUNTERS];
	uint64_t history[PON_PM_COUNTERS];
	uint64_t generation, next_period;
	uint32_t period;
	bool initialized, enabled;
};

void pon_pm_init(struct pon_pm_port ports[PON_PM_PORTS]);
int pon_pm_object(const struct pon_oam_request *request);
int pon_pm_sample(struct pon_pm_port *port, const char *device, unsigned int index, uint64_t now);
int pon_pm_get(struct pon_pm_port *port, uint16_t leaf, uint8_t *output);
int pon_pm_set(struct pon_pm_port *port, uint16_t leaf, const uint8_t *input, size_t size, uint64_t now);
#endif
