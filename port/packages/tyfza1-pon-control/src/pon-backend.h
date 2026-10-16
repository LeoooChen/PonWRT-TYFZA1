/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef PON_BACKEND_H
#define PON_BACKEND_H
#include "pon-oam.h"
#include "pon-alarm.h"
#include "pon-pm.h"
struct pon_backend {
	char device[512];
	unsigned int llid;
	uint64_t epoch;
	struct pon_alarm_table alarms;
	int alarm_error;
	struct pon_pm_port pm[PON_PM_PORTS];
	int pm_error;
};
int pon_backend_init(struct pon_backend *backend, const char *mpcp_path);
void pon_backend_bind(struct pon_oam_identity *identity, struct pon_backend *backend);
#endif
