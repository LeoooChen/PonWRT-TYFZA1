/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_CFO_H
#define RTL_AX_AP_CFO_H
#include "power.h"

struct rtl_ax_cfo {
	bool initialized, adjusting, timer_enabled, manual_tolerance, dynamic_acceleration, track_data;
	u8 crystal_default, crystal_current, crystal_upper, crystal_lower;
	s8 crystal_offset;
	u8 threshold[4], step[4], enable_threshold, stop_threshold;
	u8 fine_tune, tx_comp_threshold, digital_shift, period_count;
	u8 multi_station_mode, source, acceleration_mode, state;
	u16 timer_period, trigger_frame_threshold;
	u8 damping[16], divergence[8];
};

static inline void rtl_ax_cfo_initialize(const struct rtl_ax_io *io,
	unsigned int chip, unsigned int crystal_cap, struct rtl_ax_cfo *cfo)
{
	static const u8 thresholds[4] = { 12, 40, 80, 120 };
	static const u8 steps[4] = { 1, 1, 3, 3 };
	unsigned int i;

	/* Actual halbb_cfo_trk_init@28e2c4 takes the original flash xcap
	 * from dev_cap. This initializer does not call set_crystal_cap. */
	cfo->crystal_default = cfo->crystal_current = crystal_cap & 127;
	cfo->crystal_upper = cfo->crystal_default + 50;
	if (cfo->crystal_upper > 127)
		cfo->crystal_upper = 127;
	cfo->crystal_lower = cfo->crystal_default > 50 ? cfo->crystal_default - 50 : 1;
	cfo->adjusting = false;
	cfo->crystal_offset = 0;
	for (i = 0; i < 4; i++) {
		cfo->threshold[i] = thresholds[i];
		cfo->step[i] = steps[i];
	}
	cfo->enable_threshold = cfo->stop_threshold = 12;
	cfo->fine_tune = cfo->tx_comp_threshold = 20;
	cfo->multi_station_mode = 2;
	cfo->manual_tolerance = false;
	rtl_ax_update32(io, 0x14264, 0xff, 0);
	/* Actual digital_cfo_comp_init@28e03c and CLIENT CR@29094c. */
	rtl_ax_update32(io, 0x14494, 0, RTL_AX_BIT(29));
	rtl_ax_update32(io, 0x14490, 0x0f000000, 0x08000000);
	/* d248 is a MAC power register. C selects digital compensation
	 * for its DBCC/2TX hardware issue; XB selects hardware CFO. */
	rtl_ax_update32(io, 0xd248, 7, chip == RTL_AX_8852C ? 0 : 6);
	cfo->digital_shift = 12;
	cfo->period_count = 15;
	cfo->timer_period = 250;
	cfo->timer_enabled = false;
	cfo->trigger_frame_threshold = 300;
	cfo->acceleration_mode = 1;
	cfo->state = 0;
	cfo->source = 1;
	/* Actual module tests dev_cap RFE<=49; both board radios are RFE54. */
	cfo->dynamic_acceleration = false;
	cfo->track_data = false;
	for (i = 0; i < sizeof(cfo->damping); i++)
		cfo->damping[i] = 0;
	for (i = 0; i < sizeof(cfo->divergence); i++)
		cfo->divergence[i] = 0;
	cfo->initialized = true;
}
#endif
