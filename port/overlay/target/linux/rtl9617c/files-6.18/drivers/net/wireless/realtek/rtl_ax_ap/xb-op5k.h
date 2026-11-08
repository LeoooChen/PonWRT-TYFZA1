/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_XB_OP5K_H
#define RTL_AX_AP_XB_OP5K_H
#include "rf-runtime.h"

struct rtl_ax_xb_op5k {
	u8 defaults[2], result[2], thermal[2], history[2][2], index;
	bool channel_ready, adjusted;
	u32 runs;
};

static inline void rtl_ax_xb_op5k_reset(const struct rtl_ax_io *io)
{
	rtl_ax_channel_field(io, 0x030c, 0xff000000, 15);
	io->delay_us(io->ctx, 1);
	rtl_ax_channel_field(io, 0x030c, 0xff000000, 3);
	rtl_ax_channel_field(io, 0x032c, 0xffff0000, 0);
	io->delay_us(io->ctx, 1);
	rtl_ax_channel_field(io, 0x032c, 0xffff0000, 0x40);
	rtl_ax_channel_field(io, 0xc0f8, 6, 3);
	rtl_ax_channel_field(io, 0xc1f8, 6, 3);
	io->delay_us(io->ctx, 1);
	rtl_ax_channel_field(io, 0xc0f8, 6, 2);
	rtl_ax_channel_field(io, 0xc1f8, 6, 2);
	io->delay_us(io->ctx, 1);
}

/* Actual trigger@33e978 scans sixteen settings on both CV1 ADC paths.
 * Choose the first longest zero-counter interval; accept its midpoint only
 * with at least three settings and within five of the previous setting. */
static inline int rtl_ax_xb_op5k_run(const struct rtl_ax_io *io, struct rtl_ax_xb_op5k *state)
{
	u8 retry[2] = {}, counts[2][16], backup[2];
	int ret = 0;

	while (retry[0] < 2 || retry[1] < 2) {
		for (unsigned int path = 0; path < 2; path++)
			backup[path] = (io->read32(io->ctx, 0x1c0d8 + path * 256) >> 5) & 15;
		rtl_ax_channel_field(io, 0xc0d4, BIT(4), 1);
		rtl_ax_channel_field(io, 0xc1d4, BIT(4), 1);
		rtl_ax_channel_field(io, 0x12b8, BIT(30), 1);
		rtl_ax_channel_field(io, 0x32b8, BIT(30), 1);
		rtl_ax_xb_op5k_reset(io);
		for (unsigned int i = 0; i < 16; i++)
			for (unsigned int path = 0; path < 2; path++) {
				rtl_ax_channel_field(io, 0xc0d8 + path * 256, 0x1e0, i);
				counts[path][i] = (io->read32(io->ctx, 0x1c0fc + path * 256) >> 20) & 255;
				rtl_ax_xb_op5k_reset(io);
			}
		for (unsigned int path = 0; path < 2; path++) {
			unsigned int length = 0, best = 0, first = 0, last = 0;
			for (unsigned int i = 0; i < 16; i++) {
				length = counts[path][i] ? 0 : length + 1;
				if (length > best) { best = length; first = i + 1 - length; last = i; }
			}
			unsigned int center = (first + last) / 2;
			if (best >= 3 && abs((int)center - backup[path]) <= 5) {
				state->result[path] = center;
				retry[path] = 2;
			} else {
				state->result[path] = backup[path];
				retry[path]++;
			}
			rtl_ax_channel_field(io, 0xc0d8 + path * 256, 0x1e0, state->result[path]);
			ret = rtl_ax_rf_thermal(io, path, &state->thermal[path]);
			if (ret) break;
		}
		rtl_ax_channel_field(io, 0xc0d4, BIT(4), 0);
		rtl_ax_channel_field(io, 0xc1d4, BIT(4), 0);
		rtl_ax_channel_field(io, 0x12b8, BIT(30), 0);
		rtl_ax_channel_field(io, 0x32b8, BIT(30), 0);
		if (ret) return ret;
	}
	state->runs++;
	return 0;
}

/* Original bw_setting@33f7bc remembers the bandwidth-specific ADC default. */
static inline void rtl_ax_xb_op5k_channel(const struct rtl_ax_io *io,
	struct rtl_ax_xb_op5k *state, unsigned int cv)
{
	if (!cv) return;
	for (unsigned int path = 0; path < 2; path++)
		state->defaults[path] = (io->read32(io->ctx, 0x1c0d8 + path * 256) >> 5) & 15;
	state->channel_ready = true;
	if (state->adjusted)
		for (unsigned int path = 0; path < 2; path++)
			rtl_ax_channel_field(io, 0xc0d8 + path * 256, 0x1e0, state->result[path]);
}

static inline int rtl_ax_xb_op5k_track(const struct rtl_ax_io *io,
	struct rtl_ax_xb_op5k *state)
{
	u8 thermal[2];
	unsigned int used = 0, sum[2] = {};
	int ret;

	if (!state->channel_ready) return 0;
	for (unsigned int path = 0; path < 2; path++) {
		ret = rtl_ax_rf_thermal(io, path, &thermal[path]);
		if (ret) return ret;
		state->history[path][state->index] = thermal[path];
	}
	state->index ^= 1;
	for (unsigned int i = 0; i < 2; i++) {
		if (!state->history[0][i] && !state->history[1][i]) continue;
		used++;
		for (unsigned int path = 0; path < 2; path++) sum[path] += state->history[path][i];
	}
	if (used)
		for (unsigned int path = 0; path < 2; path++) thermal[path] = sum[path] / used;
	/* Actual init@33e918 sets both base and last-calibration meters26.
	 * Track@33f668 uses a strict delta>5, only while below that base. */
	if (thermal[0] < 26 || thermal[1] < 26) {
		if (abs((int)thermal[0] - state->thermal[0]) > 5 ||
		    abs((int)thermal[1] - state->thermal[1]) > 5) {
			ret = rtl_ax_xb_op5k_run(io, state);
			if (ret) return ret;
			state->adjusted = true;
		}
	} else if (state->adjusted) {
		for (unsigned int path = 0; path < 2; path++)
			rtl_ax_channel_field(io, 0xc0d8 + path * 256, 0x1e0, state->defaults[path]);
		state->adjusted = false;
	}
	return 0;
}
#endif
