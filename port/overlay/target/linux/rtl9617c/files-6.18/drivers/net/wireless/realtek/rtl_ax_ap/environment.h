/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_ENVIRONMENT_H
#define RTL_AX_AP_ENVIRONMENT_H
#include "power.h"

struct rtl_ax_environment_write {
	u16 address;
	u32 mask, value;
};

/* Actual CLIENT CR@2ae66c, applied in env_mntr_init@2acfa0 order.
 * Its set_reg_phy0_1@2b0f54 reads/writes one 0x10000 BB window. */
static const struct rtl_ax_environment_write rtl_ax_environment_start[] = {
	{ 0x0c00, 0x00000001, 0x00000001 }, /* CCX enable */
	{ 0x0c00, 0x00000002, 0x00000000 }, /* CCX trigger option */
	{ 0x0c00, 0x00000070, 0x00000000 }, /* CCX EDCCA BW20_0 */
	{ 0x4448, 0xe0000000, 0x80000000 }, /* CCX DCCL full bandwidth source4 */
	{ 0x0c00, 0x00001000, 0x00001000 }, /* CLM enable */
	{ 0x0c08, 0x00040000, 0x00040000 }, /* NHM enable */
	{ 0x0c14, 0x00030000, 0x00010000 }, /* NHM maximum power path */
	{ 0x0c28, 0x00001000, 0x00001000 }, /* IFS enable */
	{ 0x0c2c, 0x00008000, 0x00008000 }, /* IFS threshold0 enable */
	{ 0x0c30, 0x00008000, 0x00008000 }, /* IFS threshold1 enable */
	{ 0x0c34, 0x00008000, 0x00008000 }, /* IFS threshold2 enable */
	{ 0x0c38, 0x00008000, 0x00008000 }, /* IFS threshold3 enable */
	{ 0x0c18, 0x00000001, 0x00000001 }, /* FAHM enable */
	{ 0x0c18, 0x00000002, 0x00000002 }, /* FAHM OFDM enable */
	{ 0x0c18, 0x00000004, 0x00000004 }, /* FAHM CCK enable */
	{ 0x0c1c, 0x00000070, 0x00000010 }, /* FAHM maximum power path */
	{ 0x0c28, 0x00000100, 0x00000100 }, /* FAHM disable count for each MPDU */
	{ 0x0c04, 0x00040000, 0x00040000 }, /* EDCCA CLM enable */
};

struct rtl_ax_environment {
	bool initialized, ccx_ongoing, fahm_ongoing, manual_control;
	u8 ccx_unit, clm_input, nhm_include_cca;
	bool clm_nav, clm_rssi, nhm_nav, nhm_rssi;
	u8 clm_rssi_threshold, nhm_rssi_threshold;
	u8 application[5], nhm_threshold[11], fahm_threshold[11];
	u16 monitor_time[5], previous_false_alarms;
};

static inline void rtl_ax_environment_initialize(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_environment *environment)
{
	/* Actual .rodata.str1.8+0xa3380, shared NHM/FAHM initial thresholds. */
	static const u8 thresholds[11] = { 18, 21, 24, 27, 30, 35, 40, 45, 50, 55, 60 };
	unsigned int i;

	environment->ccx_ongoing = environment->fahm_ongoing = false;
	environment->manual_control = false;
	environment->ccx_unit = 3;
	environment->clm_input = 8;
	environment->nhm_include_cca = 2;
	environment->clm_nav = environment->clm_rssi = true;
	environment->nhm_nav = environment->nhm_rssi = true;
	environment->clm_rssi_threshold = environment->nhm_rssi_threshold = 0;
	for (i = 0; i < 5; i++) {
		environment->application[i] = 0;
		environment->monitor_time[i] = 0;
	}
	for (i = 0; i < 11; i++) {
		u8 value = thresholds[i] * 2;

		/* Actual C branch sets last encoded threshold218; XB keeps120. */
		if (chip == RTL_AX_8852C && i == 10)
			value = 218;
		environment->nhm_threshold[i] = environment->fahm_threshold[i] = value;
	}
	for (i = 0; i < ARRAY_SIZE(rtl_ax_environment_start); i++) {
		const struct rtl_ax_environment_write *write = &rtl_ax_environment_start[i];

		rtl_ax_update32(io, 0x10000 | write->address, write->mask, write->value);
	}
	/* Threshold arrays remain host state here, as in the actual initializers.
	 * Setting monitor parameters, triggering and report collection follow. */
	environment->previous_false_alarms = 0;
	environment->initialized = true;
}
#endif
