/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_DIG_RUNTIME_H
#define RTL_AX_AP_DIG_RUNTIME_H
#include "dig.h"

/* SU station RSSI accumulator, rx_pkt_su_store_in_sta_info@296950.
 * The watchdog chooses the smoothing factor from its last packet count. */
static inline void rtl_ax_dig_rssi_sample(struct rtl_ax_dig_rssi *rssi, u8 value)
{
	if (!rssi->average)
		rssi->average = value << 4;
	else
		rssi->average += (value << (4 - rssi->factor)) -
			((rssi->average << (4 - rssi->factor)) >> 4);
	rssi->packets++;
}

static inline u8 rtl_ax_dig_rssi_update(struct rtl_ax_dig_rssi *rssi)
{
	rssi->factor = rssi->packets > 100 ? 4 : rssi->packets > 20 ? 3 :
		rssi->packets > 5 ? 2 : 1;
	rssi->packets = 0;
	return (rssi->average + 8) >> 4;
}

/* Origin DIG owns FAHM in this driver. The factory's background FAHM uses
 * priority1; DIG uses priority2. NHM has independent control/report CRs.
 * Keep acquisition, consumption and retrigger in the one serialized task. */
static inline bool rtl_ax_dig_fahm_sample(const struct rtl_ax_io *io,
	struct rtl_ax_dig *dig)
{
	u16 total = 0;
	unsigned int i, value, ratio;

	if (!dig->fahm_triggered) return false;
	dig->fahm_triggered = false;
	if (!(io->read32(io->ctx, 0x11ac0) & RTL_AX_BIT(16))) {
		dig->not_ready++;
		return false;
	}
	for (i = 0; i < 6; i++) {
		value = io->read32(io->ctx, 0x11aa8 + i * 4);
		total += (u16)value;
		total += value >> 16;
	}
	/* dig_fahm_trig@2d5978 measures 1900-15ms in 32us units. */
	ratio = total == 58906 ? 1000 : (total * 1000U + 29453) / 58906;
	if (total != 58906 && ratio == 1000) ratio = 999;
	dig->false_alarms = ratio;
	dig->reports++;
	return true;
}

static inline void rtl_ax_dig_fahm_trigger(const struct rtl_ax_io *io,
	struct rtl_ax_dig *dig, unsigned int chip, s8 loss)
{
	static const u8 thresholds[11] = { 36,42,48,54,60,70,80,90,100,110,120 };
	unsigned int i, address, shift;

	if (!dig->fahm_configured) {
		rtl_ax_update32(io, 0x10c18, 0xffff0000, 58906U << 16);
		rtl_ax_update32(io, 0x10c18, 0xc00, 3U << 10);
		rtl_ax_update32(io, 0x10c18, 0x38, 1U << 3);
		rtl_ax_update32(io, 0x10c18, 0x1c0, 4U << 6);
		dig->fahm_configured = true;
	}
	for (i = 0; i < 11; i++) {
		unsigned int threshold = thresholds[i];

		address = i < 2 ? 0x10c1c : i < 6 ? 0x10c20 : i < 10 ? 0x10c24 : 0x10c28;
		shift = i < 2 ? 16 + i * 8 : i == 10 ? 0 : ((i - 2) % 4) * 8;
		if (chip == RTL_AX_8852C && i == 10) threshold = 218;
		else threshold = (u8)(threshold - loss);
		rtl_ax_update32(io, address, 255U << shift, threshold << shift);
	}
	rtl_ax_update32(io, 0x10c18, RTL_AX_BIT(0), 0);
	rtl_ax_update32(io, 0x10c18, RTL_AX_BIT(0), RTL_AX_BIT(0));
	dig->fahm_triggered = true;
}

/* Actual recorder/damping@2d4d2c/2d4e70: recognize repeated up/down
 * oscillation, then retain its IGI floor until RSSI moves >3dB or 60s. */
static inline void rtl_ax_dig_damping(struct rtl_ax_dig *dig, u32 now)
{
	u8 *igi = dig->igi_history;
	u16 *fa = dig->fa_history, *threshold = dig->unit[0].fa_threshold;
	unsigned int i, previous = igi[0], pattern = 0, distance;
	bool match = false;

	for (i = 5; i; i--) {
		igi[i] = igi[i - 1];
		fa[i] = fa[i - 1];
	}
	igi[0] = dig->unit[0].igi;
	fa[0] = dig->false_alarms;
	dig->direction = (dig->direction << 1) | (previous < igi[0]);
	if (!dig->linked) return;
	if (dig->damping_active) {
		distance = dig->rssi_min > dig->damping_rssi ?
			dig->rssi_min - dig->damping_rssi : dig->damping_rssi - dig->rssi_min;
		if (distance > dig->damping_count_limit || now > dig->damping_time + 60)
			dig->damping_active = false;
		return;
	}
	if ((dig->direction & 15) == 5) pattern = 2;
	else if ((dig->direction & 31) == 9) pattern = 3;
	else if ((dig->direction & 63) == 17) pattern = 4;
	if (pattern && igi[0] > igi[1] + 1 && igi[pattern] > igi[pattern + 1] + 1) {
		match = fa[0] < threshold[0] && fa[1] >= threshold[pattern - 1];
		for (i = 2; i <= pattern; i++) match &= fa[i] < threshold[0];
		if (pattern == 2) match = fa[0] < threshold[0] && fa[1] >= threshold[1] &&
			fa[2] < threshold[0] && fa[3] >= threshold[1];
	}
	if (match) {
		dig->damping_active = true;
		dig->damping_igi = igi[0];
		dig->damping_rssi = dig->rssi_min;
		dig->damping_time = now;
	}
}

static inline u8 rtl_ax_dig_igi(const struct rtl_ax_dig *dig, u8 previous, s8 offset)
{
	unsigned int lower = dig->igi_rssi > 9 ? dig->igi_rssi - 10 : 0;
	unsigned int upper = lower + 25;
	u8 igi = previous + offset;

	if (dig->damping_enabled && dig->damping_active && lower < dig->damping_igi)
		lower = dig->damping_igi;
	if (lower < dig->unit[0].absolute_min) lower = dig->unit[0].absolute_min;
	if (upper > dig->unit[0].absolute_max) upper = dig->unit[0].absolute_max;
	if (igi < lower) igi = lower;
	if (igi > upper) igi = upper;
	return igi;
}

static inline void rtl_ax_dig_configure(const struct rtl_ax_io *io,
	struct rtl_ax_dig *dig, u8 igi)
{
	unsigned int gain = dig->igi_rssi + 5, i;
	bool follow;

	if (igi == dig->unit[0].igi) return;
	dig->unit[0].igi = igi;
	if (gain > igi) gain = igi;
	gain -= gain > dig->unit[0].pd_offset ? dig->unit[0].pd_offset : gain;
	dig->pd_requested = 110 - gain;
	rtl_ax_dig_bandwidth(io, dig, dig->bandwidth);
	follow = dig->unit[0].dynamic_pd && igi > dig->igi_rssi;
	if (follow == dig->unit[0].follow_pagc) return;
	for (i = 0; i < 4; i++) {
		static const u32 address[] = { 0x14c24,0x14c28,0x14ce8,0x14cec };

		rtl_ax_update32(io, address[i], RTL_AX_BIT(5), follow ? RTL_AX_BIT(5) : 0);
	}
	dig->unit[0].follow_pagc = follow;
}

/* Origin-mode watchdog@2d6eb4. Link changes reset the recorder and select
 * the factory's linked/unlinked FA thresholds before the next measurement. */
static inline void rtl_ax_dig_update(const struct rtl_ax_io *io,
	struct rtl_ax_dig *dig, unsigned int chip, bool linked, u8 rssi_min, s8 loss, u32 now)
{
	static const u16 thresholds[2][4] = { {196,352,440,528}, {4,8,12,16} };
	unsigned int i;
	s8 offset;

	dig->rssi_min = rssi_min;
	dig->igi_rssi = linked ? rssi_min >> 1 : 38;
	dig->updates++;
	if (linked != dig->linked) {
		dig->linked = linked;
		for (i = 0; i < 4; i++) dig->unit[0].fa_threshold[i] = thresholds[linked][i];
		for (i = 0; i < 6; i++) dig->igi_history[i] = dig->fa_history[i] = 0;
		dig->direction = 0;
		dig->damping_active = false;
		rtl_ax_dig_configure(io, dig, rtl_ax_dig_igi(dig, dig->igi_rssi, 0));
		return;
	}
	if (rtl_ax_dig_fahm_sample(io, dig)) {
		u16 *fa = dig->unit[0].fa_threshold;

		rtl_ax_dig_damping(dig, now);
		offset = dig->false_alarms < fa[0] ? -2 : dig->false_alarms < fa[1] ? 0 :
			dig->false_alarms < fa[2] ? 2 : dig->false_alarms < fa[3] ? 4 : 6;
		rtl_ax_dig_configure(io, dig, rtl_ax_dig_igi(dig, dig->unit[0].igi, offset));
	}
	rtl_ax_dig_fahm_trigger(io, dig, chip, loss);
}
#endif
