/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_C_GAPK_H
#define RTL_AX_AP_C_GAPK_H
#include "c-iqk.h"

/* Actual C GAPK SCC5G/RFE54: main@314ec0, track@3135a4,
 * power@3143f4, do_non_dbcc@3152a8. Original selected table0. */
static const u32 rtl_ax_c_gapk_kip[] = { 0x813c, 0x8124, 0x8120, 0xc0c4, 0xc0e8, 0xc0d4, 0xc0d8 };
static const u32 rtl_ax_c_gapk_rf[] = { 0, 5, 0x10005, 0xdf };

struct rtl_ax_c_gapk {
	bool ready, done[2][5];
	u8 channel;
	u16 first[2][5], second[2][5], first_reads[2][5], second_reads[2][5];
	s8 delta[2][2][17], adjustment[2][2][17];
	u32 kip_saved[2][7], kip_restored[2][7], rf_saved[2][4], rf_restored[2][4];
};

#define C_GAPK_RF(a, m, v) do { \
	ret = rtl_ax_phy_rf_write_masked(io, path, (a), (m), (v)); \
	if (ret) return ret; \
} while (0)
#define C_GAPK_READ(a, v) do { \
	ret = rtl_ax_phy_rf_read(io, path, (a), &(v)); \
	if (ret) return ret; \
} while (0)

static inline void rtl_ax_c_gapk_shot(const struct rtl_ax_io *io, unsigned int path,
	unsigned int slot, u32 command, struct rtl_ax_c_gapk *state)
{
	unsigned int i;
	u16 value;

	/* Actual do_one_shot@30e114 and before@30df14. Counter starts1
	 * and stops at2000:1999 observations. The return value reflects
	 * second80fc completion, even if the firstbff8 poll timed out. */
	if (io->read32(io->ctx, 0x1bff8) & 255) {
		rtl_ax_channel_field(io, 0x8010, 255, 0);
		io->delay_us(io->ctx, 1);
	}
	io->write32(io->ctx, 0x18000, command + path * 16);
	for (i = 0; i < 1999; i++) {
		value = io->read32(io->ctx, 0x1bff8) & 255;
		state->first_reads[path][slot]++;
		if (value == 0x55) break;
		io->delay_us(io->ctx, 10);
	}
	state->first[path][slot] = value;
	io->delay_us(io->ctx, 1);
	for (i = 0; i < 1999; i++) {
		value = io->read32(io->ctx, 0x180fc) & 65535;
		state->second_reads[path][slot]++;
		if (value == 0x8000) break;
		io->delay_us(io->ctx, 1);
	}
	state->second[path][slot] = value;
	state->done[path][slot] = value == 0x8000;
	io->delay_us(io->ctx, 1);
	rtl_ax_channel_field(io, 0x8010, 255, 0);
}

static inline int rtl_ax_c_gapk_prepare(const struct rtl_ax_io *io, unsigned int path)
{
	u32 clock = 0x12a0 + path * 0x2000;
	int ret;

	C_GAPK_RF(0x10005, 1, 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(16 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(20 + path), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(24 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(28 + path), 0);
	io->write32(io->ctx, (0x5670 + path * 0x2000) | 0x10000, 0xf801fffd);
	/* Original GAPK AFE has no IQK's additional5670 DAC/ADC writes. */
	rtl_ax_channel_field(io, clock, BIT(15), 0);
	rtl_ax_channel_field(io, clock, 0x7000, 7);
	rtl_ax_channel_field(io, clock, BIT(15), 1);
	rtl_ax_c_iqk_rxclock(io, path, 3);
	/* Actual312868 overrides B only after its ADC cfg. */
	if (path) {
		rtl_ax_channel_field(io, 0xc1d4, 0x0c000000, 1);
		rtl_ax_channel_field(io, 0xc1d8, 0x1e0, 11);
	}
	rtl_ax_channel_field(io, 0x12b8 + path * 0x2000, BIT(30), 1);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 31);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 19);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 1);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 0x41);
	rtl_ax_channel_field(io, 0x20fc, BIT(20 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(28 + path), 1);
	C_GAPK_RF(5, 0xfffff, 0);
	io->write32(io->ctx, 0x18008, 0x80);
	io->write32(io->ctx, 0x18088, 0x81ff010a);
	C_GAPK_RF(0, 0xf0000, 5);
	C_GAPK_RF(0, 0x3e0, 18);
	C_GAPK_RF(0xdf, 0x1000, 1);
	C_GAPK_RF(0x9e, 0x100, 0);
	C_GAPK_RF(0x8c, 15, 2);
	C_GAPK_RF(0x98, 0xf000, 3);
	C_GAPK_RF(0x92, 1, 0);
	C_GAPK_RF(0x92, 1, 1);
	io->delay_us(io->ctx, 100);
	C_GAPK_RF(0x92, 1, 0);
	return 0;
}

static inline void rtl_ax_c_gapk_report(const struct rtl_ax_io *io, unsigned int path,
	unsigned int kind, struct rtl_ax_c_gapk *state)
{
	unsigned int bank, item;
	u32 value;

	io->write32(io->ctx, 0x180d4, 0x00130000);
	for (bank = 0; bank < 4; bank++) {
		rtl_ax_channel_field(io, 0x80e4, 0xf00, bank + 3);
		value = io->read32(io->ctx, 0x180fc);
		for (item = 0; item < 4; item++)
			state->delta[path][kind][bank * 4 + item] = sign_extend32((value >> (item * 7)) & 127, 6);
	}
	rtl_ax_channel_field(io, 0x80e4, 0xf00, 7);
	state->delta[path][kind][16] = sign_extend32(io->read32(io->ctx, 0x180fc) & 127, 6);
	for (bank = 0; bank < 4; bank++) {
		rtl_ax_channel_field(io, 0x80e4, 0xf00, bank + 9);
		value = io->read32(io->ctx, 0x180fc);
		for (item = 0; item < 4; item++)
			state->adjustment[path][kind][bank * 4 + item] =
				sign_extend32((value >> (item * 8 + 1)) & 63, 5);
	}
	rtl_ax_channel_field(io, 0x80e4, 0xf00, 13);
	state->adjustment[path][kind][16] = sign_extend32((io->read32(io->ctx, 0x180fc) >> 1) & 63, 5);
}

static inline void rtl_ax_c_gapk_track(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_c_gapk *state)
{
	u32 itqt = 0x81cc + path * 0x100, control = 0x5670 + path * 0x2000;

	/* Actual RFE54 band5G track wrapper selects2aa8, independently
	 * of lowerRFE channel-specific6aa8/9550/d550. */
	rtl_ax_channel_field(io, 0x80e4, 63, 1);
	rtl_ax_channel_field(io, 0x801c, 0xe0000, 2);
	rtl_ax_channel_field(io, 0x80e0, 0x1f0, 7);
	rtl_ax_channel_field(io, 0x80e0, 0xf000, 0);
	rtl_ax_channel_field(io, 0x8038, 0x3f0000, 36);
	rtl_ax_channel_field(io, 0x8158 + path * 0x100, 0x1fffff, 0x2aa8);
	rtl_ax_channel_field(io, control, 2, 1);
	rtl_ax_channel_field(io, itqt, 63, 9);
	rtl_ax_channel_field(io, 0x802c, 0x0fff0000, 9);
	rtl_ax_c_gapk_shot(io, path, 0, 0x1019, state);
	rtl_ax_channel_field(io, itqt, 63, 63);
	rtl_ax_c_gapk_shot(io, path, 1, 0x1519, state);
	rtl_ax_channel_field(io, control, 2, 0);
	rtl_ax_channel_field(io, 0x80e0, 1, 0);
	rtl_ax_c_gapk_report(io, path, 0, state);
}

static inline void rtl_ax_c_gapk_power(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_c_gapk *state)
{
	u32 itqt = 0x81cc + path * 0x100, control = 0x5670 + path * 0x2000;

	rtl_ax_channel_field(io, 0x80e0, 0x1f0, 7);
	rtl_ax_channel_field(io, 0x8038, 0x3f0000, 9);
	rtl_ax_channel_field(io, 0x8170 + path * 0x100, 0x1fffff, 0);
	rtl_ax_channel_field(io, control, 2, 1);
	io->write32(io->ctx, itqt | 0x10000, 9);
	rtl_ax_channel_field(io, 0x802c, 0x0fff0000, 9);
	rtl_ax_c_gapk_shot(io, path, 2, 0x1119, state);
	rtl_ax_channel_field(io, itqt, 63, 63);
	rtl_ax_c_gapk_shot(io, path, 3, 0x1619, state);
	rtl_ax_channel_field(io, control, 2, 0);
	rtl_ax_channel_field(io, 0x801c, 0xe0000, 0);
	rtl_ax_c_gapk_report(io, path, 1, state);
}

static inline int rtl_ax_c_gapk_kmod_restore(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_c_gapk *state)
{
	int ret;

	rtl_ax_c_gapk_shot(io, path, 4, 0x1219, state);
	io->write32(io->ctx, 0x18008, 0);
	io->write32(io->ctx, 0x18088, 0x80000000);
	C_GAPK_RF(0xef, 4, 0);
	C_GAPK_RF(0, 0xf0000, 3);
	C_GAPK_RF(5, 0xfffff, 1);
	rtl_ax_channel_field(io, 0x12b8 + path * 0x2000, BIT(30), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(16 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(20 + path), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(24 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(28 + path), 0);
	io->write32(io->ctx, (0x5670 + path * 0x2000) | 0x10000, 0);
	rtl_ax_channel_field(io, 0x12a0 + path * 0x2000, 0xff000, 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(16 + path), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(24 + path), 0);
	C_GAPK_RF(0x10005, 1, 1);
	return 0;
}

static inline int rtl_ax_c_gapk_start(const struct rtl_ax_io *io, unsigned int center,
	struct rtl_ax_c_gapk *state)
{
	unsigned int path, i;
	int ret = 0, step_ret;

	state->ready = false;
	state->channel = center;
	memset(state->first_reads, 0, sizeof(state->first_reads));
	memset(state->second_reads, 0, sizeof(state->second_reads));
	/* Original backup both paths before either measurement. RF5 is
	 * skipped by the backup/reload helpers and forcedautomatic at end. */
	for (path = 0; path < 2; path++) {
		for (i = 0; i < ARRAY_SIZE(rtl_ax_c_gapk_kip); i++)
			state->kip_saved[path][i] = io->read32(io->ctx, (rtl_ax_c_gapk_kip[i] + path * 0x100) | 0x10000);
		for (i = 0; i < ARRAY_SIZE(rtl_ax_c_gapk_rf); i++) {
			if (rtl_ax_c_gapk_rf[i] == 5) continue;
			C_GAPK_READ(rtl_ax_c_gapk_rf[i], state->rf_saved[path][i]);
		}
	}
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 0xd3, 0x100, 0);
		if (!ret) ret = rtl_ax_phy_rf_write_masked(io, path, 0x18, BIT(19), 0);
		if (!ret) ret = rtl_ax_phy_rf_write_masked(io, path, 0x10018, BIT(19), 0);
		if (!ret) ret = rtl_ax_phy_rf_write_masked(io, path, 0xd3, 0x100, 1);
		rtl_ax_channel_field(io, path ? 0x4a9c : 0x4730, BIT(31), 0);
		if (!ret) ret = rtl_ax_c_gapk_prepare(io, path);
		if (!ret) {
			rtl_ax_c_gapk_track(io, path, state);
			rtl_ax_c_gapk_power(io, path, state);
			ret = rtl_ax_c_gapk_kmod_restore(io, path, state);
		}
		if (ret) break;
	}
	for (path = 0; path < 2; path++) {
		for (i = 0; i < ARRAY_SIZE(rtl_ax_c_gapk_kip); i++) {
			u32 address = (rtl_ax_c_gapk_kip[i] + path * 0x100) | 0x10000;

			io->write32(io->ctx, address, state->kip_saved[path][i]);
			state->kip_restored[path][i] = io->read32(io->ctx, address);
		}
		for (i = 0; i < ARRAY_SIZE(rtl_ax_c_gapk_rf); i++) {
			if (rtl_ax_c_gapk_rf[i] == 5) continue;
			step_ret = rtl_ax_phy_rf_write_masked(io, path, rtl_ax_c_gapk_rf[i], 0xfffff, state->rf_saved[path][i]);
			if (step_ret) ret = step_ret;
			step_ret = rtl_ax_phy_rf_read(io, path, rtl_ax_c_gapk_rf[i], &state->rf_restored[path][i]);
			if (step_ret) ret = step_ret;
		}
		step_ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 1);
		if (step_ret) ret = step_ret;
		rtl_ax_channel_field(io, path ? 0x4a9c : 0x4730, BIT(31), 1);
	}
	state->ready = !ret;
	return ret;
}
#undef C_GAPK_RF
#undef C_GAPK_READ
#endif
