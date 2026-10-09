/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_DIG_H
#define RTL_AX_AP_DIG_H
#include "power.h"

struct rtl_ax_dig_unit {
	u8 gaincode[3], force_gaincode[3];
	u8 absolute_max, absolute_min, state_limit, pd_offset, igi;
	bool dynamic_pd, follow_pagc;
	u16 fa_threshold[4];
};

struct rtl_ax_dig {
	bool initialized, damping_enabled, damping_active, fahm_triggered;
	u8 mode, previous_mode, igi_rssi, damping_count_limit;
	u8 max_gaincode[3], ib_backoff;
	s8 ib_peak_power, lna_gain[2][7], tia_gain[2][2];
	u16 tdma_period, fahm_time;
	u8 fahm_level, fahm_application, fahm_numerator, fahm_denominator;
	struct rtl_ax_dig_unit unit[2];
	u8 pd_requested, ofdm_encoded, cck_encoded, bandwidth;
};

static inline void rtl_ax_dig_initialize(const struct rtl_ax_io *io,
					 struct rtl_ax_dig *dig)
{
	static const s8 lna[7] = { -24, -16, -8, 0, 8, 16, 24 };
	static const s8 tia[2][2] = { { 12, 20 }, { 16, 24 } };
	static const u16 fa[4] = { 196, 352, 440, 528 };
	unsigned int band, i, lower, upper, rssi_floor, pd_gain, adjusted;

	/* Actual halbb_dig_init@2d69f8, normal unlinked PHY0, DIG_ORIGIN.
	 * Its CLIENT CR initializer@2d8374 does not populate IB power or
	 * LNA/TIA error CRs (the corresponding SDK block is #if 0). Their
	 * zero masks give zero corrections in gain_para_init@2d6054. */
	dig->mode = dig->previous_mode = 0;
	dig->unit[0].state_limit = 3;
	dig->unit[1].state_limit = 1;
	dig->tdma_period = 50;
	dig->fahm_level = dig->fahm_application = 2;
	dig->fahm_time = 1900;
	dig->fahm_numerator = 1;
	dig->fahm_denominator = 4;
	dig->fahm_triggered = false;
	dig->ib_peak_power = 0;
	dig->ib_backoff = 0;
	for (band = 0; band < 2; band++) {
		for (i = 0; i < 7; i++)
			dig->lna_gain[band][i] = lna[i];
		for (i = 0; i < 2; i++)
			dig->tia_gain[band][i] = tia[band][i];
	}
	/* halbb_get_lna/tia/rxb_idx reads the actual path A initial indices. */
	dig->max_gaincode[0] = (io->read32(io->ctx, 0x1472c) >> 24) & 7;
	dig->max_gaincode[1] = (io->read32(io->ctx, 0x1473c) >> 9) & 1;
	dig->max_gaincode[2] = (io->read32(io->ctx, 0x146a8) >> 10) & 31;
	for (i = 0; i < 2; i++) {
		unsigned int index;
		struct rtl_ax_dig_unit *unit = &dig->unit[i];

		for (index = 0; index < 3; index++)
			unit->gaincode[index] = unit->force_gaincode[index] = dig->max_gaincode[index];
		unit->absolute_max = i ? 38 : 90;
		unit->absolute_min = 12;
		unit->pd_offset = 16;
		unit->igi = 0;
		unit->follow_pagc = false;
	}
	/* dig_reset@2d678c: disable OFDM/CCK lower bounds. follow_pagc
	 * already equals false, so the original does not write its four CRs. */
	rtl_ax_update32(io, 0x1481c, RTL_AX_BIT(29), 0);
	rtl_ax_update32(io, 0x14b74, RTL_AX_BIT(30), 0);
	for (i = 0; i < 2; i++) {
		dig->unit[i].dynamic_pd = true;
		for (band = 0; band < 4; band++)
			dig->unit[i].fa_threshold[band] = fa[band];
	}
	/* Actual initial RSSI is 28; reset passes 38 to the disabled PD
	 * functions. Preserve this distinction from the nearby SDK comment. */
	dig->igi_rssi = 28;
	dig->damping_enabled = true;
	dig->damping_active = false;
	dig->damping_count_limit = 6;
	rssi_floor = dig->igi_rssi > 9 ? dig->igi_rssi - 10 : 0;
	lower = rssi_floor > dig->unit[0].absolute_min ? rssi_floor : dig->unit[0].absolute_min;
	upper = rssi_floor + 25;
	if (upper > dig->unit[0].absolute_max)
		upper = dig->unit[0].absolute_max;
	dig->unit[0].igi = dig->igi_rssi;
	if (dig->unit[0].igi < lower)
		dig->unit[0].igi = lower;
	if (dig->unit[0].igi > upper)
		dig->unit[0].igi = upper;
	/* dyn_pd_th_ofdm/cck: max(IGI,RSSI+5)-min(offset,that).
	 * Initial bandwidth is 20MHz (index0); channel lifecycle follows. */
	dig->bandwidth = 0;
	pd_gain = dig->igi_rssi + 5;
	if (pd_gain < dig->unit[0].igi)
		pd_gain = dig->unit[0].igi;
	pd_gain -= pd_gain > dig->unit[0].pd_offset ? dig->unit[0].pd_offset : pd_gain;
	dig->pd_requested = 110 - pd_gain;
	/* C@2e1658 and XB@2ec06c both add 7dB, round up to even,
	 * then encode (102-adjusted)/2 in 481c[10:6]. */
	adjusted = (dig->pd_requested + 7 + 1) & ~1U;
	dig->ofdm_encoded = (102 - adjusted) / 2;
	rtl_ax_update32(io, 0x1481c, 0x7c0, dig->ofdm_encoded << 6);
	rtl_ax_update32(io, 0x1481c, 0, RTL_AX_BIT(29));
	/* C@2e1828 uses PHY0 common writes; XB@2ec23c direct writes.
	 * Both add 5dB, set signed CCK threshold, high threshold127, enable. */
	dig->cck_encoded = -(dig->pd_requested + 5);
	rtl_ax_update32(io, 0x14b64, 0xff000000, (u32)dig->cck_encoded << 24);
	rtl_ax_update32(io, 0x14b64, 0x00ff0000, 0x007f0000);
	rtl_ax_update32(io, 0x14b74, 0, RTL_AX_BIT(30));
	dig->initialized = true;
}
#endif
