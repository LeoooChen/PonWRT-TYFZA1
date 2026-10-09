/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_POWER_TABLES_H
#define RTL_AX_AP_POWER_TABLES_H
#include <linux/types.h>
#include "phy.h"

/* Actual halrf_power_by_rate_store_to_array has three145-byte band arrays.
 * Offsets140..144 are HE/DCM,VHT,HT,OFDM,CCK adjustments in quarter-dBm. */
struct rtl_ax_power_stage {
	s8 rate[3][145];
	s8 track_cck[2][2][30], track_2g[2][2][30];
	s8 track_5g[2][2][3][30], track_6g[2][2][4][30];
	unsigned int rate_rows, track_rows;
};
struct rtl_ax_power_inputs {
	struct rtl_ax_power_stage stage[RTL_AX_PHY_STAGES];
	bool ready;
};
int rtl_ax_power_inputs_load(struct device *dev, unsigned int chip,
			     struct rtl_ax_power_inputs *inputs);
#endif
