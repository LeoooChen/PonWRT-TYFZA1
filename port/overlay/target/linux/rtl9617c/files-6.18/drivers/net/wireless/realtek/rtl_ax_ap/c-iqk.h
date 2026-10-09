/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_C_IQK_H
#define RTL_AX_AP_C_IQK_H
#include "xb-iqk.h"

/* Actual C host IQK normal SCC/5G/20MHz: generic doiQK@30a91c,
 * chip callbacks3178ac/31abdc/31a914/317ba0/31a63c/31a70c. */
static const u32 rtl_ax_c_iqk_bb_regs[] = {
	0x2344, 0xc0c4, 0xc0d4, 0xc0d8, 0xc0e8, 0xc1d4, 0xc1d8, 0xc1e8,
};

struct rtl_ax_c_iqk {
	bool ready, narrow[2], lok_range_fail[2], tx_fail[2], rx_fail[2];
	u8 channel, times, thermal[2], shots[2], lok_attempts[2];
	u32 failures, lok_dac[2][4], lok_idac[2], lok_vbuffer[2];
	u32 iqc_enable[2], tx_result[2], rx_result[2], nb_tx[2], nb_rx[2], status;
	u32 bb_saved[2][8], bb_restored[2][8], rf_saved[2][7], rf_restored[2][7];
	u16 command[2][32], first_status[2][32], second_status[2][32];
	u16 first_reads[2][32], second_reads[2][32];
	bool shot_fail[2][32];
};

#define C_IQK_RF(a, m, v) do { \
	ret = rtl_ax_phy_rf_write_masked(io, path, (a), (m), (v)); \
	if (ret) return ret; \
} while (0)
#define C_IQK_READ(a, v) do { \
	ret = rtl_ax_phy_rf_read(io, path, (a), &(v)); \
	if (ret) return ret; \
} while (0)

static inline bool rtl_ax_c_iqk_shot(const struct rtl_ax_io *io, unsigned int path,
	u32 command, bool rf_control, unsigned int initial_delay, struct rtl_ax_c_iqk *state)
{
	unsigned int slot = state->shots[path]++, i;
	u16 value;
	bool fail = true;

	if (rf_control)
		rtl_ax_channel_field(io, 0x5670 + path * 0x2000, 2, 1);
	state->command[path][slot] = command | BIT(4 + path);
	io->write32(io->ctx, 0x18000, (command | BIT(4 + path)) + 1);
	if (initial_delay)
		io->delay_us(io->ctx, initial_delay);
	/* Actual check_cal_C@315f98 always performs both polls. First:
	 * max821 reads,820/821 failed delays10us. Second:max21 reads.
	 * First timeout does not replace the actual second completion/fail. */
	for (i = 0; i < 821; i++) {
		value = io->read32(io->ctx, 0x1bff8) & 255;
		state->first_reads[path][slot]++;
		io->delay_us(io->ctx, 10);
		if (value == 0x55)
			break;
	}
	state->first_status[path][slot] = value;
	for (i = 0; i < 21; i++) {
		value = io->read32(io->ctx, 0x180fc) & 65535;
		state->second_reads[path][slot]++;
		io->delay_us(io->ctx, 10);
		if (value == 0x8000) {
			fail = false;
			break;
		}
	}
	state->second_status[path][slot] = value;
	io->delay_us(io->ctx, 10);
	if (!fail)
		fail = !!(io->read32(io->ctx, 0x18008) & BIT(26));
	rtl_ax_channel_field(io, 0x8010, 255, 0);
	if (rf_control)
		rtl_ax_channel_field(io, 0x5670 + path * 0x2000, 2, 0);
	state->shot_fail[path][slot] = fail;
	return fail;
}

static inline void rtl_ax_c_iqk_rxclock(const struct rtl_ax_io *io, unsigned int path,
	unsigned int selector)
{
	u32 clock = 0x12a0 + path * 0x2000, base = 0xc000 + path * 0x100;

	/* Actual rxck_force_C@30f5d0 -> adc_cfg_C@2e333c. Common
	 * analog fields precede selector3/2 digital timing fields. */
	rtl_ax_channel_field(io, clock, BIT(19), 0);
	rtl_ax_channel_field(io, clock, 0x70000, selector);
	rtl_ax_channel_field(io, clock, BIT(19), 1);
	rtl_ax_channel_field(io, base + 0xd4, 0x780, 8);
	rtl_ax_channel_field(io, base + 0xe8, 0xffff0000, 9);
	rtl_ax_channel_field(io, base + 0xe4, 0x30, 2);
	rtl_ax_channel_field(io, clock, 0xff800000, 73);
	rtl_ax_channel_field(io, base + 0xec, 0x6000, 0);
	rtl_ax_channel_field(io, base + 0xd4, 0x7800, selector == 3 ? 0 : 2);
	rtl_ax_channel_field(io, base + 0xd4, 0x0c000000, 2);
	rtl_ax_channel_field(io, base + 0xd8, 0x1e0, selector == 3 ? 4 : 8);
	rtl_ax_channel_field(io, base + 0xc4, 0x3e0000, selector == 3 ? 6 : 0);
	rtl_ax_channel_field(io, base + 0xec, 0xff0000, 3);
}

static inline int rtl_ax_c_iqk_thermal(const struct rtl_ax_io *io, unsigned int path,
	u8 *temperature)
{
	u32 value;
	int ret;

	C_IQK_RF(0x42, BIT(19), 1);
	C_IQK_RF(0x42, BIT(19), 0);
	C_IQK_RF(0x42, BIT(19), 1);
	io->delay_us(io->ctx, 200);
	C_IQK_READ(0x42, value);
	*temperature = (value >> 1) & 63;
	return 0;
}

static inline int rtl_ax_c_iqk_prepare(const struct rtl_ax_io *io, unsigned int path)
{
	u32 clock = 0x12a0 + path * 0x2000, control = 0x5670 + path * 0x2000;
	int ret;

	C_IQK_RF(0x10005, 1, 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(16 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(20 + path), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(24 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(28 + path), 0);
	rtl_ax_channel_field(io, path ? 0x4a9c : 0x4730, BIT(31), 0);
	io->write32(io->ctx, control | 0x10000, 0xf801fffd);
	rtl_ax_channel_field(io, control, BIT(14), 1);
	rtl_ax_channel_field(io, control, BIT(31), 1);
	rtl_ax_channel_field(io, clock, BIT(15), 0);
	rtl_ax_channel_field(io, clock, 0x7000, 7);
	rtl_ax_channel_field(io, clock, BIT(15), 1);
	rtl_ax_channel_field(io, control, BIT(13), 1);
	rtl_ax_c_iqk_rxclock(io, path, 3);
	rtl_ax_channel_field(io, control, 0x60000000, 2);
	rtl_ax_channel_field(io, 0x12b8 + path * 0x2000, BIT(30), 1);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 31);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 19);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 1);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 0x41);
	rtl_ax_channel_field(io, 0x20fc, BIT(20 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(28 + path), 1);
	/* Actual preset_C@31a914 contains only BB page selection; this
	 * board ELF does not perform the SDK RF55/RF18 page writes. */
	rtl_ax_channel_field(io, 0x8104 + path * 0x100, 1, 0);
	rtl_ax_channel_field(io, 0x8154 + path * 0x100, 8, 0);
	io->write32(io->ctx, (0x810c + path * 0x100) | 0x10000, 0x33112211);
	C_IQK_RF(5, 0xfffff, 0);
	io->write32(io->ctx, 0x18008, 0x80);
	io->write32(io->ctx, 0x18088, 0x81ff010a);
	return 0;
}

static inline int rtl_ax_c_iqk_lok5(const struct rtl_ax_io *io, unsigned int path,
	unsigned int bias, struct rtl_ax_c_iqk *state)
{
	static const u8 track[] = { 0, 4, 6, 7 }, low[] = { 0, 4, 8, 12, 14, 16, 18 }, high[] = { 20, 22, 23 };
	static const u8 gain[] = { 18, 6, 18, 21, 21, 6 }, itqt[] = { 27, 9, 27, 27, 27, 9 };
	static const u16 command[] = { 0x708, 0x508, 0x308, 0x508, 0x508, 0x508 };
	unsigned int i, j;
	u32 value;
	int ret;

	C_IQK_RF(0xef, 2, 1);
	C_IQK_RF(0x30, 0x8000, 1);
	C_IQK_RF(0x30, 0x38, bias);
	C_IQK_RF(0xef, 2, 0);
	C_IQK_RF(0x64, 0x38000, 0);
	C_IQK_RF(0x7f, 0x80, 1);
	C_IQK_RF(0x65, 0xf0000, 15);
	C_IQK_RF(0x55, 0x1e, 0);
	C_IQK_RF(0xef, 4, 1);
	C_IQK_RF(0, 0xfffff, 0x403e1); /* Original SCC syn1to2=1. */
	io->delay_us(io->ctx, 10);
	C_IQK_RF(0x11, 3, 0);
	C_IQK_RF(0x11, 0x70, 6);
	for (i = 0; i < 6; i++) {
		if (i == 4) C_IQK_RF(0x11, 0x70, 7);
		C_IQK_RF(0x11, 0x1f000, gain[i]);
		rtl_ax_channel_field(io, 0x81cc + path * 0x100, 63, itqt[i]);
		if (!i) rtl_ax_channel_field(io, 0x802c, 0xfff, 0x21);
		rtl_ax_c_iqk_shot(io, path, command[i], true, 0, state);
		if (i >= 2) {
			C_IQK_READ(8, value);
			state->lok_dac[path][i == 5 ? 2 : i == 4 ? 3 : i - 2] = value;
		}
	}
	C_IQK_RF(0x55, 6, 3);
	for (i = 0; i < 4; i++) {
		C_IQK_RF(0x11, 0x70, track[i]);
		for (j = 0; j < ARRAY_SIZE(low); j++) {
			C_IQK_RF(0x11, 0x1f000, low[j]);
			C_IQK_RF(8, 0xfffff, state->lok_dac[path][i == 3 ? 2 : 0]);
		}
		for (j = 0; j < ARRAY_SIZE(high); j++) {
			C_IQK_RF(0x11, 0x1f000, high[j]);
			C_IQK_RF(8, 0xfffff, state->lok_dac[path][i == 3 ? 3 : 1]);
		}
	}
	C_IQK_READ(0x5c, value);
	state->lok_idac[path] = value;
	/* Actual fine-check@3158bc subtracts3 and compares unsigned57:
	 * 3..60, not this older SDK's 3..53. The actual start function has
	 * no additional first-DAC bounds check. */
	state->lok_range_fail[path] = ((value >> 14) & 63) < 3 || ((value >> 14) & 63) > 60 ||
		((value >> 8) & 63) < 3 || ((value >> 8) & 63) > 60;
	C_IQK_READ(0x0a, value);
	state->lok_vbuffer[path] = value;
	C_IQK_RF(0xef, 4, 0);
	return 0;
}

static inline int rtl_ax_c_iqk_tx5(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_c_iqk *state)
{
	static const u8 tracks[] = { 3, 6, 6 }, gains[] = { 18, 10, 15 };
	unsigned int first = state->narrow[path] ? 2 : 0, group;
	bool fail = false;
	int ret;

	for (group = first; group < 3; group++) {
		C_IQK_RF(0x11, 3, 0);
		C_IQK_RF(0x11, 0x70, tracks[group]);
		C_IQK_RF(0x11, 0x1f000, gains[group]);
		io->write32(io->ctx, (0x81cc + path * 0x100) | 0x10000, state->narrow[path] ? 27 : 18);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 0x100, 1);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 16, 1);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 4, 0);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 3, group + 1);
		if (!state->narrow[path]) io->write32(io->ctx, 0x1808c, 0x10010);
		rtl_ax_channel_field(io, 0x8010, 255, 0);
		fail = rtl_ax_c_iqk_shot(io, path, state->narrow[path] ? 0x408 : 0x808, false, 1, state);
		if (!state->narrow[path]) rtl_ax_channel_field(io, 0x9fe0, BIT(8 + group + path * 4), fail);
	}
	/* Actual wide TX returns the last group's flag, not OR of all three. */
	state->tx_fail[path] = fail;
	state->nb_tx[path] = state->narrow[path] && !fail ?
		io->read32(io->ctx, (0x8138 + path * 0x100) | 0x10000) | 2 : 0x40000002;
	return 0;
}

static inline int rtl_ax_c_iqk_rx5(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_c_iqk *state)
{
	static const u16 gain[] = { 0x190, 0x310 };
	unsigned int idx, group, first = state->narrow[path] ? 1 : 0;
	u32 value, saved_mode, control = 0x5670 + path * 0x2000;
	bool fail = false;
	int ret;

	rtl_ax_channel_field(io, 0x20fc, 0xffff0000, path ? 0x202 : 0x101);
	rtl_ax_channel_field(io, control, BIT(13), 1);
	rtl_ax_c_iqk_rxclock(io, path, 2);
	rtl_ax_channel_field(io, control, 0x60000000, 0);
	C_IQK_RF(0x8f, 0x1000, 1);
	rtl_ax_channel_field(io, 0x12b8 + path * 0x2000, BIT(30), 1);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 15);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 3);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 1);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 0x41);
	rtl_ax_channel_field(io, 0x20fc, 0xffff0000, path ? 0x2202 : 0x1101);
	C_IQK_READ(0, value);
	saved_mode = (value >> 14) & 3;
	if (path) {
		C_IQK_RF(0xdc, 0x300, 3);
		C_IQK_READ(0x17, value);
		C_IQK_RF(0x1f, 0x30000, (value >> 10) & 3);
		C_IQK_RF(0x1f, 0x3ff, value & 0x3ff);
	}
	C_IQK_RF(0, 0xf0000, 12);
	C_IQK_RF(0, 0xc000, 0);
	C_IQK_RF(0x89, 15, 8);
	io->delay_us(io->ctx, 10);
	for (idx = first; idx < 2; idx++) {
		group = idx * 2;
		C_IQK_RF(0, 0x3ff0, gain[idx]);
		C_IQK_RF(0x8c, 0xf0, 0);
		rtl_ax_channel_field(io, 0x8044, 0xe000, idx ? 6 : 4);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 0x100, 1);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 16, 0);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 7, group);
		C_IQK_RF(0x1e, 0xfffff, 0x80013);
		io->delay_us(io->ctx, 100);
		io->delay_us(io->ctx, 100);
		C_IQK_READ(0, value);
		rtl_ax_channel_field(io, 0x8024, 0xfffff, value);
		rtl_ax_channel_field(io, control, 2, 1);
		rtl_ax_channel_field(io, 0x802c, 0x0fff0000, 0x11);
		rtl_ax_c_iqk_shot(io, path, 0x708, true, 1, state);
		io->delay_us(io->ctx, 10);
		if (!state->narrow[path])
			fail |= rtl_ax_c_iqk_shot(io, path, 0xc08, true, 1, state);
	}
	if (state->narrow[path]) {
		unsigned int x, y;

		C_IQK_RF(0x1e, 63, 0x13);
		io->delay_us(io->ctx, 100);
		io->delay_us(io->ctx, 100);
		rtl_ax_channel_field(io, 0x802c, 0x0fff0000, 0x11);
		rtl_ax_c_iqk_shot(io, path, 0x608, true, 1, state);
		value = io->read32(io->ctx, (0x813c + path * 0x100) | 0x10000);
		x = abs((int)(value >> 20) - 1024);
		y = (value >> 8) & 4095;
		if (y > 2048) y = 4096 - y;
		fail = x > 150 || y > 150;
		state->nb_rx[path] = fail ? 0x40000002 : value | 2;
	} else {
		state->nb_rx[path] = fail ? 0x40000002 : 0x40000000;
	}
	if (path) C_IQK_RF(0xdc, 0x300, 0);
	C_IQK_RF(0, 0xc000, saved_mode);
	C_IQK_RF(0x1e, 0x80000, 0);
	state->rx_fail[path] = fail;
	return 0;
}

static inline int rtl_ax_c_iqk_restore(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_c_iqk *state)
{
	unsigned int i;
	int ret, result = 0;

	rtl_ax_c_iqk_shot(io, path, 0x1208, false, 200, state);
	rtl_ax_channel_field(io, 0x8010, 255, 0);
	io->write32(io->ctx, 0x18008, 0);
	io->write32(io->ctx, 0x18088, 0x80000000);
	ret = rtl_ax_phy_rf_write_masked(io, path, 0xef, 4, 0);
	if (ret) result = ret;
	ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xf0000, 3);
	if (ret) result = ret;
	ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 1);
	if (ret) result = ret;
	rtl_ax_channel_field(io, 0x12b8 + path * 0x2000, BIT(30), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(16 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(20 + path), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(24 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(28 + path), 0);
	io->write32(io->ctx, (0x5670 + path * 0x2000) | 0x10000, 0);
	rtl_ax_channel_field(io, 0x12a0 + path * 0x2000, 0xff000, 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(16 + path), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(24 + path), 0);
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x10005, 1, 1);
	if (ret) result = ret;
	rtl_ax_channel_field(io, path ? 0x4a9c : 0x4730, BIT(31), 1);
	for (i = 0; i < ARRAY_SIZE(rtl_ax_c_iqk_bb_regs); i++) {
		io->write32(io->ctx, rtl_ax_c_iqk_bb_regs[i] | 0x10000, state->bb_saved[path][i]);
		state->bb_restored[path][i] = io->read32(io->ctx, rtl_ax_c_iqk_bb_regs[i] | 0x10000);
	}
	for (i = 0; i < ARRAY_SIZE(rtl_ax_xb_iqk_rf_regs); i++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, rtl_ax_xb_iqk_rf_regs[i], 0xfffff, state->rf_saved[path][i]);
		if (ret) result = ret;
		ret = rtl_ax_phy_rf_read(io, path, rtl_ax_xb_iqk_rf_regs[i], &state->rf_restored[path][i]);
		if (ret) result = ret;
	}
	ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 1);
	return result ? result : ret;
}

static inline int rtl_ax_c_iqk_start20(const struct rtl_ax_io *io, unsigned int center,
	struct rtl_ax_c_iqk *state)
{
	unsigned int path, i;
	u32 value, afe_bit;
	int ret, restore_ret;

	state->ready = false;
	state->channel = center;
	memset(state->shots, 0, sizeof(state->shots));
	memset(state->first_reads, 0, sizeof(state->first_reads));
	memset(state->second_reads, 0, sizeof(state->second_reads));
	io->write32(io->ctx, 0x19fe0, 0);
	for (path = 0; path < 2; path++) {
		C_IQK_READ(0x18, value);
		rtl_ax_channel_field(io, 0x9fe0, 0xff000000, 24);
		rtl_ax_channel_field(io, 0x9fe4, 15U << (path * 16), 1);
		rtl_ax_channel_field(io, 0x9fe4, 0xf0U << (path * 16), 0);
		rtl_ax_channel_field(io, 0x9fe4, 0xff00U << (path * 16), center);
		rtl_ax_channel_field(io, 0x9fe8, 255, 19);
		for (i = 0; i < ARRAY_SIZE(rtl_ax_c_iqk_bb_regs); i++)
			state->bb_saved[path][i] = io->read32(io->ctx, rtl_ax_c_iqk_bb_regs[i] | 0x10000);
		for (i = 0; i < ARRAY_SIZE(rtl_ax_xb_iqk_rf_regs); i++)
			C_IQK_READ(rtl_ax_xb_iqk_rf_regs[i], state->rf_saved[path][i]);
		ret = rtl_ax_c_iqk_prepare(io, path);
		if (!ret) ret = rtl_ax_c_iqk_thermal(io, path, &state->thermal[path]);
		afe_bit = !!(io->read32(io->ctx, (0xc0e8 + path * 0x100) | 0x10000) & 64);
		state->narrow[path] = state->thermal[path] < 20;
		if (state->narrow[path]) rtl_ax_channel_field(io, 0xc0e8 + path * 0x100, 64, 1);
		for (i = 0; !ret && i < 2; i++) {
			state->lok_attempts[path] = i + 1;
			ret = rtl_ax_c_iqk_lok5(io, path, i ? 7 : 3, state);
			if (!state->lok_range_fail[path]) break;
		}
		if (!ret) ret = rtl_ax_c_iqk_tx5(io, path, state);
		if (!ret) ret = rtl_ax_c_iqk_rx5(io, path, state);
		if (!ret) ret = rtl_ax_c_iqk_thermal(io, path, &state->thermal[path]);
		if (!ret) {
			rtl_ax_channel_field(io, 0x9fe0, BIT(2 + path * 4), state->tx_fail[path]);
			rtl_ax_channel_field(io, 0x9fe0, BIT(3 + path * 4), state->rx_fail[path]);
			state->iqc_enable[path] = io->read32(io->ctx, (0x8124 + path * 0x100) | 0x10000);
			state->tx_result[path] = io->read32(io->ctx, (0x8138 + path * 0x100) | 0x10000);
			state->rx_result[path] = io->read32(io->ctx, (0x813c + path * 0x100) | 0x10000);
			rtl_ax_channel_field(io, 0x9fe8, 0xff00, state->times);
			if ((io->read32(io->ctx, 0x19fe0) >> (path * 4)) & 15) state->failures++;
			rtl_ax_channel_field(io, 0x9fe8, 0xff0000U << (path * 4), state->failures);
			io->write32(io->ctx, (0x8138 + path * 0x100) | 0x10000, state->narrow[path] ? state->nb_tx[path] : 0x40000000);
			io->write32(io->ctx, (0x813c + path * 0x100) | 0x10000, state->narrow[path] ? state->nb_rx[path] : 0x40000000);
			rtl_ax_channel_field(io, 0xc0e8 + path * 0x100, 64, afe_bit);
		}
		restore_ret = rtl_ax_c_iqk_restore(io, path, state);
		state->times++;
		if (ret || restore_ret) return ret ? ret : restore_ret;
	}
	state->status = io->read32(io->ctx, 0x19fe0);
	state->ready = true;
	return 0;
}
#undef C_IQK_RF
#undef C_IQK_READ
#endif
