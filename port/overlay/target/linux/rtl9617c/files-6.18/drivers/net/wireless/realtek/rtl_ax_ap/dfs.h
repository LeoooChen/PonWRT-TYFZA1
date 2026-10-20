/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_DFS_H
#define RTL_AX_AP_DFS_H
#include <linux/types.h>
#include <linux/string.h>
#include <linux/unaligned.h>
#include "power.h"

/* Original TYFZA1 Gen1 report units:PW200ns,PRI25us. Its older PPB
 * thresholds differ from the newer GPL SDK's Gen1 and Gen2 profiles. */
struct rtl_ax_dfs_profile {
	u8 pw_min[8];
	u16 pw_max[8];
	u8 pri_min[8], pri_max[8], pulses[8];
	bool long_pulse;
};
struct rtl_ax_dfs {
	struct rtl_ax_dfs_profile profile;
	u8 region, ppb_parameter, pw_factor, pri_factor, false_report_threshold;
	u8 throughput_threshold, nhm_threshold, pri_mask, fake_radar_threshold;
	u16 pw_diff, pri_diff, long_chirp_diff;
	u8 adaptive_initial_count, adaptive_add_count, adaptive_count_threshold;
	u8 adaptive_count, adaptive_total, adaptive_state;
	u8 aci_wait, aci_wait_threshold, aci_high, aci_low;
	s8 aci;
	bool aci_pending;
	u16 idle_period_threshold, false_alarm_threshold, max_pulse_width;
	bool initialized, dynamic_setting, in_cac, first_dynamic, dynamic_reset;
	bool enabled, hardware_enabled, detected, two_segments, bypass_segment0, w53, chirp_started[2];
	u8 primary, bandwidth, long_count[2];
	u32 chirp_start[2];
	u64 reports, pulses_seen, events, malformed, dropped;
	int error;
};

static inline void rtl_ax_dfs_region(struct rtl_ax_dfs *dfs, u8 region)
{
	/* Exact parameters assembled by rgn_dmn_dflt_cnfg@29e138. Domain4
	 * isKCC;MKK2 has separate channel-dependent W53/W56 configuration. */
	static const struct rtl_ax_dfs_profile fcc = {
		{5,5,30,55,5,5,250,0}, {5,25,50,100,5,5,500,1000},
		{20,6,8,8,13,57,40,0}, {123,10,20,20,14,58,80,0},
		{18,23,16,12,15,18,15,255}, true,
	};
	static const struct rtl_ax_dfs_profile etsi = {
		{2,2,2,100,2,2,5,5}, {25,75,150,150,15,15,5,5},
		{40,25,12,10,100,33,57,40}, {200,200,18,20,134,100,58,40},
		{9,15,25,20,20,30,18,18}, false,
	};
	static const struct rtl_ax_dfs_profile kcc = {
		{5,5,2,0,0,0,0,0}, {5,5,75,1000,1000,1000,1000,1000},
		{57,22,121,0,0,0,0,0}, {58,23,122,0,0,0,0,0},
		{18,10,20,255,255,255,255,255}, false,
	};

	dfs->region = region;
	if (region == 1)
		dfs->profile = fcc;
	else if (region == 3)
		dfs->profile = etsi;
	else if (region == 4)
		dfs->profile = kcc;
}

static inline void rtl_ax_dfs_initialize(struct rtl_ax_dfs *dfs, unsigned int region)
{
	/* Actual dfs_init@29e498 initializes host parameters only. Radar
	 * enable/report/CAC and pulse processing belong to the AP lifecycle. */
	rtl_ax_dfs_region(dfs, region);
	dfs->dynamic_setting = true;
	dfs->ppb_parameter = 5;
	dfs->pw_factor = 1;
	dfs->pri_factor = 1;
	dfs->false_report_threshold = 5;
	dfs->throughput_threshold = 2;
	dfs->idle_period_threshold = 35;
	dfs->false_alarm_threshold = 50;
	dfs->nhm_threshold = 2;
	dfs->max_pulse_width = 660;
	dfs->pri_mask = 3;
	dfs->fake_radar_threshold = 4;
	dfs->pw_diff = 18;
	dfs->pri_diff = 80;
	dfs->long_chirp_diff = 20;
	dfs->adaptive_initial_count = 1;
	dfs->adaptive_add_count = 1;
	dfs->adaptive_count_threshold = 30;
	dfs->adaptive_count = dfs->adaptive_total = dfs->adaptive_state = 0;
	dfs->aci = -128;
	dfs->aci_pending = true;
	dfs->aci_wait = 0;
	dfs->aci_wait_threshold = 5;
	dfs->aci_high = 3;
	dfs->aci_low = 0;
	dfs->in_cac = true;
	dfs->first_dynamic = true;
	dfs->dynamic_reset = true;
	dfs->initialized = true;
}

/* MKK W53/W56 tables from TYFZA1 rgn_dmn_cnfg_by_ch@29e5fc. */
static inline void rtl_ax_dfs_channel(struct rtl_ax_dfs *dfs,
	unsigned int region, unsigned int primary, unsigned int bandwidth)
{
	static const struct rtl_ax_dfs_profile w53 = {
		{2,2,100,100,150,150,150,150}, {25,75,550,550,160,160,160,160},
		{40,25,3,3,2,2,2,2}, {200,200,200,200,36,43,44,54},
		{10,15,16,16,16,16,16,16}, false,
	};
	static const struct rtl_ax_dfs_profile w56 = {
		{2,5,10,5,30,55,250,5}, {3,5,11,25,50,100,500,5},
		{55,57,80,6,8,8,40,13}, {56,58,160,10,20,20,80,14},
		{18,18,10,23,16,12,15,18}, true,
	};

	rtl_ax_dfs_region(dfs, region);
	dfs->w53 = false;
	if (region == 2) {
		bool low = primary >= 52 && primary <= 64;

		if (bandwidth == 3 && primary >= 36 && primary <= 48)
			low = true;
		dfs->profile = low ? w53 : w56;
		dfs->w53 = low;
	}
	dfs->primary = primary;
	dfs->bandwidth = bandwidth;
	dfs->two_segments = bandwidth == 3;
	dfs->bypass_segment0 = dfs->two_segments && region != 1 && primary <= 64;
	memset(dfs->long_count, 0, sizeof(dfs->long_count));
	memset(dfs->chirp_started, 0, sizeof(dfs->chirp_started));
	dfs->detected = false;
}

/* halbb_dfs_dyn_setting@2a1240, with the board's default runtime policy.
 * Inputs are the preceding watchdog window's measured Mbps/NHM/FA values. */
static inline void rtl_ax_dfs_dynamic(struct rtl_ax_dfs *dfs, u32 throughput,
	u8 idle_ratio, u8 noise_ratio, u32 false_alarms)
{
	bool noisy;
	unsigned int count;

	if (!dfs->dynamic_setting) return;
	if (dfs->aci_pending) {
		dfs->aci = -128;
		dfs->aci_wait++;
	} else {
		dfs->aci_pending = true;
		dfs->aci_wait = 0;
	}
	if (dfs->first_dynamic) {
		dfs->first_dynamic = false;
		return;
	}
	/* Default dyn_setting uses these thresholds both during and after CAC.
	 * Its separate debug-force branch is not the CAC flag. */
	dfs->aci_high = dfs->bandwidth ? 5 : 12;
	dfs->aci_low = dfs->bandwidth ? 2 : 9;
	if (throughput < dfs->throughput_threshold) {
		dfs->pw_diff = dfs->long_chirp_diff = 18;
		dfs->pri_diff = 80;
		dfs->pw_factor = dfs->pri_factor = 1;
		dfs->ppb_parameter = 5;
	} else {
		dfs->pw_diff = dfs->long_chirp_diff = 20;
		dfs->pri_diff = 170;
		dfs->pw_factor = dfs->pri_factor = 3;
		dfs->ppb_parameter = 4;
	}
	if (!(dfs->region == 3 && dfs->primary == 60 && !dfs->bandwidth))
		dfs->pri_diff = 1;
	noisy = (idle_ratio && idle_ratio < dfs->idle_period_threshold) ||
		false_alarms > dfs->false_alarm_threshold ||
		(idle_ratio <= 97 && !throughput) ||
		(noise_ratio != 100 && noise_ratio > dfs->nhm_threshold);
	if (!dfs->adaptive_state) {
		if (dfs->aci > dfs->aci_high || noisy) {
			dfs->adaptive_count = dfs->adaptive_initial_count;
			dfs->adaptive_total++;
			dfs->adaptive_state = 1;
		}
	} else if (dfs->adaptive_state == 1) {
		if (dfs->adaptive_count) dfs->adaptive_count--;
		if (dfs->aci > dfs->aci_low || noisy) {
			count = dfs->adaptive_count + dfs->adaptive_add_count;
			dfs->adaptive_count = count > 255 ? 255 : count;
			if (dfs->adaptive_total < 255) dfs->adaptive_total++;
		}
		if (!dfs->adaptive_count) dfs->adaptive_state = 0;
	} else {
		dfs->adaptive_state = 0;
		dfs->adaptive_count = 0;
	}
}

/* parsing_aci2sig@2a1150: signed7-bit IE24 value, adjusted for bandwidth. */
static inline void rtl_ax_dfs_aci(struct rtl_ax_dfs *dfs, u8 raw)
{
	int value = raw & 127;

	if (value & 64) value -= 128;
	dfs->aci = value - (!dfs->bandwidth ? 30 : dfs->bandwidth == 1 ? 40 : 50);
	dfs->aci_pending = false;
}

/* cfg_dfs@229864 and en_bbrpt@2296f4: MACband0, report threshold61,
 * timeout80ms, DLE selector0. BB CLIENT radar enable isBB0 bit31. */
static inline int rtl_ax_dfs_hardware(const struct rtl_ax_io *io, bool enable)
{
	unsigned int value, saved;
	int ret;

	rtl_ax_update32(io, 0x8404, 0, RTL_AX_BIT(17));
	rtl_ax_update32(io, 0x8400, 0, RTL_AX_BIT(17));
	if (enable)
		io->write32(io->ctx, 0x9630, 0x0d41);
	else {
		saved = io->read8(io->ctx, 0x9630);
		io->write8(io->ctx, 0x9630, saved | RTL_AX_BIT(4));
		ret = io->poll32(io->ctx, 0x9630, RTL_AX_BIT(5), RTL_AX_BIT(5), 1, 3000, &value);
		if (ret) return ret;
		/* The original saves only the low byte before the stop request. */
		io->write8(io->ctx, 0x9630, saved & ~RTL_AX_BIT(0));
	}
	rtl_ax_update32(io, 0x10000, RTL_AX_BIT(31), enable ? RTL_AX_BIT(31) : 0);
	return 0;
}

struct rtl_ax_dfs_pulse { u16 width; u8 interval, sequence, segment; bool chirp; };

/* MAC DFS reports have an8-byte drop-count header and8 bytes per pulse.
 * In TW mode a zero segment0 width selects the independent segment1 record. */
static inline struct rtl_ax_dfs_pulse rtl_ax_dfs_pulse(const u8 *data, bool two)
{
	u64 word = get_unaligned_le64(data);
	struct rtl_ax_dfs_pulse pulse = {
		.width = (word >> 28) & 1023,
		.interval = ((word >> 24) & 15) << 4 | ((word >> 20) & 15),
		.sequence = (word >> 56) & 127,
		.chirp = !!(word & BIT_ULL(19)),
	};

	if (two && !pulse.width) {
		pulse.segment = 1;
		pulse.width = (word >> 9) & 1023;
		pulse.interval = (word >> 1) & 255;
		pulse.sequence = (word >> 49) & 127;
		pulse.chirp = word & 1;
	}
	return pulse;
}

struct rtl_ax_dfs_range { u16 low, high; };
struct rtl_ax_dfs_segment {
	u8 count[8], chirps;
	struct rtl_ax_dfs_range width[8], interval[8], chirp_width, long_width;
	u16 maximum;
	bool sequence_error;
};

static inline void rtl_ax_dfs_range_add(struct rtl_ax_dfs_range *range, u16 value)
{
	if (value < range->low) range->low = value;
	if (value > range->high) range->high = value;
}
static inline u16 rtl_ax_dfs_range_diff(const struct rtl_ax_dfs_range *range)
{
	return range->high - range->low;
}

/* Native pulse classification, preserving the factory's per-report window,
 * per-segment sequence handling, tolerance factors and12-second long pulse window. */
static inline bool rtl_ax_dfs_detect(struct rtl_ax_dfs *dfs, const u8 *data,
	unsigned int count, u32 seconds)
{
	struct rtl_ax_dfs_segment segment[2] = {};
	const struct rtl_ax_dfs_profile *profile = &dfs->profile;
	u8 previous[2] = {}, seen = 0, previous_segment = 0, history = 0;
	u8 saved_long[2] = {dfs->long_count[0], dfs->long_count[1]};
	unsigned int i, j, kept = 0;
	bool detected = false;

	for (i = 0; i < 2; i++) {
		segment[i].chirp_width.low = segment[i].long_width.low = 65535;
		for (j = 0; j < 8; j++)
			segment[i].width[j].low = segment[i].interval[j].low = 65535;
	}
	/* Sequence inspection precedes the PRI mask and segment0 bypass. */
	for (i = 0; i < count; i++) {
		struct rtl_ax_dfs_pulse p = rtl_ax_dfs_pulse(data + i * 8, dfs->two_segments);
		unsigned int part = p.segment;
		int delta = (int)p.sequence - previous[part];

		if (i && (seen & (1U << part)) && part == previous_segment &&
		    (previous[part] == 127 ? p.sequence != 0 : delta > 1 || delta < -1))
			segment[part].sequence_error = true;
		previous[part] = p.sequence;
		previous_segment = part;
		seen |= 1U << part;
		if ((!part && dfs->bypass_segment0) || p.interval < dfs->pri_mask)
			continue;
		kept++;
	}
	if (kept < dfs->false_report_threshold && !(profile->long_pulse && kept))
		return false;
	kept = 0;
	for (i = 0; i < count; i++) {
		struct rtl_ax_dfs_pulse p = rtl_ax_dfs_pulse(data + i * 8, dfs->two_segments);
		unsigned int part = p.segment;
		struct rtl_ax_dfs_segment *s = &segment[part];
		bool first = !kept || !(history & (1U << part));

		if ((!part && dfs->bypass_segment0) || p.interval < dfs->pri_mask)
			continue;
		kept++;
		history |= 1U << part;
		if (p.width > s->maximum) s->maximum = p.width;
		if (profile->long_pulse) {
			if (seconds - dfs->chirp_start[part] > 11) {
				dfs->chirp_started[part] = false;
				dfs->chirp_start[part] = 0;
				dfs->long_count[part] = 0;
			}
			if (p.chirp && !dfs->chirp_started[part]) {
				dfs->chirp_start[part] = seconds;
				dfs->chirp_started[part] = true;
			}
		}
		if (p.chirp) {
			s->chirps++;
			rtl_ax_dfs_range_add(&s->chirp_width, p.width);
		}
		for (j = 0; j < 8; j++) {
			int low = profile->pw_min[j] * (8 - dfs->pw_factor) / 8;
			unsigned int high = profile->pw_max[j] * (8 + dfs->pw_factor) / 8;
			unsigned int pri_low = profile->pri_min[j] * (8 - dfs->pri_factor) / 8;
			unsigned int pri_high = profile->pri_max[j] * (8 + dfs->pri_factor) / 8;

			if (low > (int)profile->pw_min[j] - 1) low = (int)profile->pw_min[j] - 1;
			if (high < profile->pw_max[j] + 1U) high = profile->pw_max[j] + 1U;
			if (pri_high > 220) pri_high = 220;
			if (p.width < (u8)low || p.width > high) continue;
			if (j == 6 && profile->long_pulse) {
				if (p.chirp && (first ? p.interval != 0 :
				    p.interval >= pri_low && p.interval <= pri_high)) {
					dfs->long_count[part]++;
					rtl_ax_dfs_range_add(&s->long_width, p.width);
				}
			} else if (p.interval >= pri_low && p.interval <= pri_high &&
				   p.chirp == ((dfs->region == 3 && j == 3) || (dfs->w53 && j >= 2))) {
				s->count[j]++;
				rtl_ax_dfs_range_add(&s->width[j], p.width);
				if (!(dfs->region == 3 && (j == 4 || j == 5)) && !first)
					rtl_ax_dfs_range_add(&s->interval[j], p.interval);
			}
		}
	}
	for (i = 0; i < (dfs->two_segments ? 2U : 1U); i++) {
		struct rtl_ax_dfs_segment *s = &segment[i];
		bool hit = false;
		bool masked = count - kept > dfs->fake_radar_threshold &&
			!(dfs->region == 3 && dfs->primary == 60 && !dfs->bandwidth);

		if (profile->long_pulse) {
			if (rtl_ax_dfs_range_diff(&s->long_width) > dfs->long_chirp_diff ||
			    rtl_ax_dfs_range_diff(&s->chirp_width) > dfs->long_chirp_diff ||
			    s->maximum > dfs->max_pulse_width || s->chirps > 4)
				dfs->long_count[i] = 0;
			if ((int)dfs->long_count[i] - saved_long[i] <= 1)
				dfs->long_count[i] = saved_long[i];
			if (masked) dfs->long_count[i] = 0;
		}
		for (j = 0; j < 8; j++) {
			unsigned int required = profile->pulses[j] * dfs->ppb_parameter / 8;

			if (j == 6 && profile->long_pulse) {
				if (dfs->long_count[i] >= required) hit = true;
			} else if (s->count[j] >= required)
				hit = rtl_ax_dfs_range_diff(&s->width[j]) <= dfs->pw_diff &&
					rtl_ax_dfs_range_diff(&s->interval[j]) <= dfs->pri_diff;
		}
		if (masked || s->maximum > dfs->max_pulse_width ||
		    rtl_ax_dfs_range_diff(&s->chirp_width) > dfs->long_chirp_diff ||
		    (dfs->bandwidth <= 2 && s->sequence_error)) hit = false;
		/* Factory adaptive rejection keeps accumulated long-pulse evidence. */
		if (dfs->adaptive_state) hit = false;
		if (hit) dfs->long_count[i] = 0;
		detected |= hit;
	}
	return detected;
}
/* Reports may contain both80MHz segments. Remove the non-DFS lower80
 * before classification, so its pulses are not counted as masked/fake pulses
 * against the still-required upper80. Caller validates the hardware61 limit. */
static inline bool rtl_ax_dfs_report(struct rtl_ax_dfs *dfs, const u8 *data,
	unsigned int count, u32 seconds)
{
	u8 selected[61 * 8];
	unsigned int used = 0;

	if (!dfs->two_segments || !dfs->bypass_segment0)
		return rtl_ax_dfs_detect(dfs, data, count, seconds);
	for (unsigned int i = 0; i < count; i++)
		if (rtl_ax_dfs_pulse(data + i * 8, true).segment) {
			memcpy(selected + used * 8, data + i * 8, 8);
			used++;
		}
	return rtl_ax_dfs_detect(dfs, selected, used, seconds);
}
#endif
