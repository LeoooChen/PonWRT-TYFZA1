/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_TSSI_DE_H
#define RTL_AX_AP_TSSI_DE_H
#include "channel.h"
#include "trim.h"
#include "board-cal.h"

struct rtl_ax_tssi_de {
	bool ready;
	u8 center, cck_group;
	u16 ofdm_group, trim_group;
	s16 cck[2], ofdm[2];
	s8 trim[2];
	s8 bw_diff[2][4];
	bool slope_no_pg;
	u16 expected[2][8], readback[2][8];
};

static inline unsigned int rtl_ax_tssi_cck_group(unsigned int ch)
{
	if (ch <= 2) return 0;
	if (ch <= 5) return 1;
	if (ch <= 8) return 2;
	if (ch <= 11) return 3;
	if (ch <= 13) return 4;
	return ch == 14 ? 5 : 0;
}

static inline unsigned int rtl_ax_tssi_ofdm_group(unsigned int ch)
{
	static const u8 low[] = { 1, 3, 6, 9, 12, 36, 44, 52, 60, 100, 108, 116, 124, 132, 140, 149, 157, 165, 173 };
	static const u8 high[] = { 2, 5, 8, 11, 14, 40, 48, 56, 64, 104, 112, 120, 128, 136, 144, 153, 161, 169, 177 };
	unsigned int i;

	/* Actual OFDM group_C@331998/XB@35e4f0 includes signed averaging
	 * between adjacent5GHz groups, encoded as two byte indices. */
	for (i = 0; i < ARRAY_SIZE(low); i++) {
		if (ch >= low[i] && ch <= high[i])
			return i;
		if (i >= 5 && i + 1 < ARRAY_SIZE(low) && ch > high[i] && ch < low[i + 1] &&
		    low[i + 1] - high[i] == 4)
			return i << 8 | (i + 1);
	}
	return 0;
}

static inline unsigned int rtl_ax_tssi_trim_group(unsigned int ch)
{
	static const u8 low[] = { 1, 9, 36, 52, 100, 116, 132, 149 };
	static const u8 high[] = { 8, 14, 48, 64, 112, 128, 144, 177 };
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(low); i++)
		if (ch >= low[i] && ch <= high[i])
			return i;
	/* Preserve this original helper's explicit interpolation cases. */
	if (ch > 48 && ch < 52) return 0x0203;
	if (ch > 120 && ch < 124) return 0x0405;
	if (ch > 161 && ch < 165) return 0x0607;
	return 0;
}

static inline s8 rtl_ax_tssi_flash_ofdm(const struct rtl_ax_board_cal *cal,
	unsigned int path, unsigned int group)
{
	if (group < 5)
		return cal->tssi_2g[path][group];
	return cal->tssi_5g[path][group - 5];
}

static inline s8 rtl_ax_tssi_flash_ofdm_de(const struct rtl_ax_board_cal *cal,
	unsigned int path, unsigned int group)
{
	if (group >= 0x0506)
		return ((int)rtl_ax_tssi_flash_ofdm(cal, path, group >> 8) +
			rtl_ax_tssi_flash_ofdm(cal, path, group & 255)) / 2;
	return rtl_ax_tssi_flash_ofdm(cal, path, group);
}

static inline s8 rtl_ax_tssi_trim_de(const struct rtl_ax_trim *trim,
	unsigned int path, unsigned int group)
{
	if (group >= 0x0203)
		return ((int)trim->tssi[path][group >> 8] + trim->tssi[path][group & 255]) / 2;
	return trim->tssi[path][group];
}

static inline void rtl_ax_xb_tssi_de(const struct rtl_ax_io *io,
	const struct rtl_ax_board_cal *cal, const struct rtl_ax_trim *trim,
	unsigned int center, struct rtl_ax_tssi_de *state)
{
	static const u16 addr[] = { 0x5858, 0x5860, 0x5838, 0x5840, 0x5848, 0x5850, 0x5828, 0x5830 };
	unsigned int path, i;

	/* Actual XB set_efuse_to_de@360ab8 normal TSSI-type path:
	 * perpath CCK(long/short),then sixMCS widths. All10-bit fields. */
	state->center = center;
	state->cck_group = rtl_ax_tssi_cck_group(center);
	state->ofdm_group = rtl_ax_tssi_ofdm_group(center);
	state->trim_group = rtl_ax_tssi_trim_group(center);
	for (path = 0; path < 2; path++) {
		state->trim[path] = rtl_ax_tssi_trim_de(trim, path, state->trim_group);
		state->cck[path] = cal->tssi_cck[path][state->cck_group] + state->trim[path];
		state->ofdm[path] = rtl_ax_tssi_flash_ofdm_de(cal, path, state->ofdm_group) + state->trim[path];
		for (i = 0; i < ARRAY_SIZE(addr); i++) {
			u32 address = addr[i] + path * 0x2000;
			u16 value = (i < 2 ? state->cck[path] : state->ofdm[path]) & 1023;

			rtl_ax_channel_field(io, address, 0x003ff000, value);
			state->expected[path][i] = value;
			state->readback[path][i] = (io->read32(io->ctx, address | 0x10000) >> 12) & 1023;
		}
	}
	state->ready = true;
}

static inline s8 rtl_ax_c_tssi_bw_diff(const struct rtl_ax_board_cal *cal,
	unsigned int path, unsigned int width, unsigned int ofdm_group)
{
	/* Original C BW group@331c80 is the same 5G ranges without the
	 * five2G entries; interpolation averages adjacent signed bytes. */
	if (ofdm_group >= 0x0506)
		return ((int)cal->tssi_bw_diff[path][width][(ofdm_group >> 8) - 5] +
			cal->tssi_bw_diff[path][width][(ofdm_group & 255) - 5]) / 2;
	return cal->tssi_bw_diff[path][width][ofdm_group - 5];
}

static inline void rtl_ax_c_tssi_slope(const struct rtl_ax_io *io,
	const struct rtl_ax_board_cal *cal, struct rtl_ax_tssi_de *state)
{
	unsigned int path, group;

	/* Original get_efuse_C@334834 sets slop_no_pg when all56 bytes of
	 * the two5G gain/CW sets are zero; set_efuse_to_slope@336a1c then
	 * returns without touching BB. This board's actual HS takes that path. */
	state->slope_no_pg = true;
	for (path = 0; path < 2; path++)
		for (group = 5; group < 19; group++)
			if (cal->tssi_ofdm_gain[path][group] || cal->tssi_ofdm_cw[path][group])
				state->slope_no_pg = false;
	if (state->slope_no_pg)
		return;
	for (path = 0; path < 2; path++) {
		u32 offset = path * 0x2000;
		unsigned int ofdm = state->ofdm_group;
		s8 gain, cw;

		if (ofdm >= 0x0506) {
			gain = ((int)cal->tssi_ofdm_gain[path][ofdm >> 8] + cal->tssi_ofdm_gain[path][ofdm & 255]) / 2;
			cw = ((int)cal->tssi_ofdm_cw[path][ofdm >> 8] + cal->tssi_ofdm_cw[path][ofdm & 255]) / 2;
		} else {
			gain = cal->tssi_ofdm_gain[path][ofdm];
			cw = cal->tssi_ofdm_cw[path][ofdm];
		}
		rtl_ax_channel_field(io, 0x562c + offset, 0x0000ff00, (u8)cal->tssi_cck_gain[path][state->cck_group]);
		rtl_ax_channel_field(io, 0x561c + offset, 0x000001ff, cal->tssi_cck_cw[path][state->cck_group] & 511);
		rtl_ax_channel_field(io, 0x5624 + offset, 0x0000ff00, (u8)gain);
		rtl_ax_channel_field(io, 0x5610 + offset, 0x0003fe00, cw & 511);
	}
}

static inline void rtl_ax_c_tssi_de20(const struct rtl_ax_io *io,
	const struct rtl_ax_board_cal *cal, const struct rtl_ax_trim *trim,
	unsigned int center, unsigned int bandwidth, struct rtl_ax_tssi_de *state)
{
	static const u16 addr[] = { 0x5858, 0x5860, 0x5838, 0x5840, 0x5848, 0x5850, 0x5828, 0x5830 };
	static const u8 order[] = { 0, 1, 2, 6, 7, 3, 4, 5 };
	static const u8 width[] = { 0, 0, 0, 1, 2, 3, 0, 0 };
	unsigned int path, i;
	s8 adc[3] = {};

	/* Actual set_efuse_to_de_C@33607c normal SCC: CCK then MCS20
	 * (including legacy20),40,80,160, perpath A then B. RFE54 at160MHz
	 * adds the signed ADC corrections from BB1a8 to20/40/80 only. */
	if (bandwidth == 3) {
		u32 value = io->read32(io->ctx, 0x101a8);

		for (i = 0; i < ARRAY_SIZE(adc); i++)
			adc[i] = value >> (i * 8);
	}
	state->center = center;
	state->cck_group = rtl_ax_tssi_cck_group(center);
	state->ofdm_group = rtl_ax_tssi_ofdm_group(center);
	state->trim_group = rtl_ax_tssi_trim_group(center);
	rtl_ax_c_tssi_slope(io, cal, state);
	for (path = 0; path < 2; path++) {
		state->trim[path] = rtl_ax_tssi_trim_de(trim, path, state->trim_group);
		state->cck[path] = cal->tssi_cck[path][state->cck_group] + state->trim[path];
		state->ofdm[path] = rtl_ax_tssi_flash_ofdm_de(cal, path, state->ofdm_group) + state->trim[path];
		for (i = 0; i < 4; i++)
			state->bw_diff[path][i] = rtl_ax_c_tssi_bw_diff(cal, path, i, state->ofdm_group);
		for (i = 0; i < ARRAY_SIZE(order); i++) {
			unsigned int slot = order[i];
			u32 address = addr[slot] + path * 0x2000;
			u16 value = (slot < 2 ? state->cck[path] :
				state->ofdm[path] + state->bw_diff[path][width[slot]] +
				(width[slot] < 3 ? adc[width[slot]] : 0)) & 1023;

			rtl_ax_channel_field(io, address, 0x003ff000, value);
			state->expected[path][slot] = value;
			state->readback[path][slot] = (io->read32(io->ctx, address | 0x10000) >> 12) & 1023;
		}
	}
	state->ready = true;
}
#endif
