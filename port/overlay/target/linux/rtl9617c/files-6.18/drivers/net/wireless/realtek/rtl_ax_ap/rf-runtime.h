/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RF_RUNTIME_H
#define RTL_AX_AP_RF_RUNTIME_H
#include "phy.h"

struct rtl_ax_rf_runtime {
	u8 thermal[2], lck_thermal[2];
	u32 periods, lck_runs, lck_timeouts;
	int result;
};

static inline int rtl_ax_rf_thermal(const struct rtl_ax_io *io, unsigned int path, u8 *thermal)
{
	u32 value;
	int ret;

	/* Actual get_thermal_C@30e790/XB@33d7a4: trigger 1/0/1,
	 * wait 200us, RF42[6:1]. This is the live meter, not flash thermal. */
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x42, BIT(19), 1);
	if (!ret) ret = rtl_ax_phy_rf_write_masked(io, path, 0x42, BIT(19), 0);
	if (!ret) ret = rtl_ax_phy_rf_write_masked(io, path, 0x42, BIT(19), 1);
	if (ret) return ret;
	io->delay_us(io->ctx, 200);
	ret = rtl_ax_phy_rf_read(io, path, 0x42, &value);
	if (!ret) *thermal = (value >> 1) & 63;
	return ret;
}

static inline int rtl_ax_rf_lck_channel(const struct rtl_ax_io *io, u32 channel)
{
	u32 value;
	unsigned int i;
	int ret, clear;

	/* Actual set_ch_lck_C@30e174/XB@33d42c, SCC path A. */
	ret = rtl_ax_phy_rf_write_masked(io, 0, 0xd3, BIT(8), 1);
	if (!ret) ret = rtl_ax_phy_rf_write_masked(io, 0, 0x18, 0xfffff, channel);
	clear = rtl_ax_phy_rf_write_masked(io, 0, 0xd3, BIT(8), 0);
	if (!ret) ret = clear;
	if (ret) return ret;
	for (i = 0; i < 1000; i++) {
		ret = rtl_ax_phy_rf_read(io, 0, 0xb7, &value);
		if (ret) return ret;
		if (!(value & BIT(8))) return 0;
		io->delay_us(io->ctx, 1);
	}
	return -ETIMEDOUT;
}

static inline int rtl_ax_rf_lck(const struct rtl_ax_io *io, unsigned int chip)
{
	u32 mode[2], control[2], channel;
	unsigned int path;
	int ret, restore = 0, error;

	/* Actual SCC LCK: save both RF0 modes and path-A RF18. XB also
	 * saves RF5; C deliberately restores RF5=1 rather than its old value. */
	if (chip == RTL_AX_8192XB)
		for (path = 0; path < 2; path++) {
			ret = rtl_ax_phy_rf_read(io, path, 5, &control[path]);
			if (ret) return ret;
		}
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_read(io, path, 0, &mode[path]);
		if (ret) return ret;
	}
	ret = rtl_ax_phy_rf_read(io, 0, 0x18, &channel);
	if (ret) return ret;
	for (path = 0; path < 2 && !ret; path++)
		ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xf0000, 3);
	for (path = 0; path < 2 && !ret; path++)
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 0);
	if (!ret) ret = rtl_ax_rf_lck_channel(io, channel);
	/* Restore the operational RF state even when calibration or RF IO
	 * fails. A timed-out calibration must not become the thermal baseline. */
	for (path = 0; path < 2; path++) {
		error = rtl_ax_phy_rf_write_masked(io, path, 0, 0xfffff, mode[path]);
		if (!restore) restore = error;
	}
	for (path = 0; path < 2; path++) {
		error = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff,
			chip == RTL_AX_8192XB ? control[path] : 1);
		if (!restore) restore = error;
	}
	return ret ? ret : restore;
}

static inline int rtl_ax_rf_runtime_update(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_rf_runtime *state)
{
	unsigned int path, delta = 0;
	int ret;

	state->periods++;
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_rf_thermal(io, path, &state->thermal[path]);
		if (ret) return ret;
		delta = max(delta, (unsigned int)abs((int)state->thermal[path] - state->lck_thermal[path]));
	}
	/* Actual C/XB lck_tracking: first nonzero reference or max delta>=8.
	 * Both board default RF capability masks enable LCK (bit2). */
	if ((state->lck_thermal[0] || state->lck_thermal[1]) && delta < 8)
		return 0;
	ret = rtl_ax_rf_lck(io, chip);
	if (ret == -ETIMEDOUT) state->lck_timeouts++;
	if (ret) return ret;
	memcpy(state->lck_thermal, state->thermal, sizeof(state->lck_thermal));
	state->lck_runs++;
	return 0;
}
#endif
