/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_GAPK_H
#define RTL_AX_AP_GAPK_H
#include "channel.h"

struct rtl_ax_gapk {
	bool initialized, xb_rfe_skip;
	u8 channel;
	u32 table_readback[2];
};

static inline int rtl_ax_gapk_initialize(const struct rtl_ax_io *io,
	struct rtl_ax_gapk *state)
{
	unsigned int path;
	int ret;

	if (state->initialized)
		return 0;
	/* Actual init_C@3157fc/XB@35b300 selects HW table0 once.
	 * Both select helpers write RF10055[19], A then B. Called by the
	 * first channel GAPK trigger, after RX_DCK and before IQK. */
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x10055, BIT(19), 0);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_read(io, path, 0x10055, &state->table_readback[path]);
		if (ret)
			return ret;
	}
	state->initialized = true;
	return 0;
}

static inline int rtl_ax_gapk_channel(const struct rtl_ax_io *io, unsigned int chip,
	unsigned int center, struct rtl_ax_gapk *state)
{
	int ret = rtl_ax_gapk_initialize(io, state);

	if (ret)
		return ret;
	state->channel = center;
	/* Actual do_txgapk_XB@35b17c returns directly for RFE>50 after
	 * saving channel info. TYFZA1 RFE54 takes that original branch.
	 * C has no such gate and its measurement remains to be ported. */
	state->xb_rfe_skip = chip == RTL_AX_8192XB;
	return 0;
}
#endif
