/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_DFS_H
#define RTL_AX_AP_DFS_H
#include <linux/types.h>
#include <linux/string.h>

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
	u16 idle_period_threshold, false_alarm_threshold, max_pulse_width;
	bool initialized, dynamic_setting, in_cac, first_dynamic, dynamic_reset;
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
	dfs->in_cac = true;
	dfs->first_dynamic = true;
	dfs->dynamic_reset = true;
	dfs->initialized = true;
}
#endif
