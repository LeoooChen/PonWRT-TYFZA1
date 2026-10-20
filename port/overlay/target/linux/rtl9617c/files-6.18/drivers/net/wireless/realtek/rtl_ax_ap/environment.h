/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_ENVIRONMENT_H
#define RTL_AX_AP_ENVIRONMENT_H
#include "power.h"

struct rtl_ax_environment_write {
	u16 address;
	u32 mask, value;
};

/* Actual CLIENT CR@2ae66c, applied in env_mntr_init@2acfa0 order.
 * Its set_reg_phy0_1@2b0f54 reads/writes one 0x10000 BB window. */
static const struct rtl_ax_environment_write rtl_ax_environment_start[] = {
	{ 0x0c00, 0x00000001, 0x00000001 }, /* CCX enable */
	{ 0x0c00, 0x00000002, 0x00000000 }, /* CCX trigger option */
	{ 0x0c00, 0x00000070, 0x00000000 }, /* CCX EDCCA BW20_0 */
	{ 0x4448, 0xe0000000, 0x80000000 }, /* CCX DCCL full bandwidth source4 */
	{ 0x0c00, 0x00001000, 0x00001000 }, /* CLM enable */
	{ 0x0c08, 0x00040000, 0x00040000 }, /* NHM enable */
	{ 0x0c14, 0x00030000, 0x00010000 }, /* NHM maximum power path */
	{ 0x0c28, 0x00001000, 0x00001000 }, /* IFS enable */
	{ 0x0c2c, 0x00008000, 0x00008000 }, /* IFS threshold0 enable */
	{ 0x0c30, 0x00008000, 0x00008000 }, /* IFS threshold1 enable */
	{ 0x0c34, 0x00008000, 0x00008000 }, /* IFS threshold2 enable */
	{ 0x0c38, 0x00008000, 0x00008000 }, /* IFS threshold3 enable */
	{ 0x0c18, 0x00000001, 0x00000001 }, /* FAHM enable */
	{ 0x0c18, 0x00000002, 0x00000002 }, /* FAHM OFDM enable */
	{ 0x0c18, 0x00000004, 0x00000004 }, /* FAHM CCK enable */
	{ 0x0c1c, 0x00000070, 0x00000010 }, /* FAHM maximum power path */
	{ 0x0c28, 0x00000100, 0x00000100 }, /* FAHM disable count for each MPDU */
	{ 0x0c04, 0x00040000, 0x00040000 }, /* EDCCA CLM enable */
};

struct rtl_ax_environment {
	bool initialized, ccx_ongoing, fahm_ongoing, manual_control;
	u8 ccx_unit, clm_input, nhm_include_cca;
	bool clm_nav, clm_rssi, nhm_nav, nhm_rssi;
	u8 clm_rssi_threshold, nhm_rssi_threshold;
	u8 application[5], nhm_threshold[11], fahm_threshold[11];
	u16 monitor_time[5], previous_false_alarms;
	bool background_started, nhm_ready;
	s8 external_loss;
	u16 nhm_period, nhm_bins[12], nhm_tx, nhm_cca, nhm_idle;
	u8 nhm_idle_ratio, nhm_noise_ratio;
	u32 false_alarms, throughput, traffic_previous;
	u64 samples, not_ready;
	u64 traffic_time_ms;
};

static inline void rtl_ax_environment_initialize(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_environment *environment)
{
	/* Actual .rodata.str1.8+0xa3380, shared NHM/FAHM initial thresholds. */
	static const u8 thresholds[11] = { 18, 21, 24, 27, 30, 35, 40, 45, 50, 55, 60 };
	unsigned int i;

	environment->ccx_ongoing = environment->fahm_ongoing = false;
	environment->manual_control = false;
	environment->ccx_unit = 3;
	environment->clm_input = 8;
	environment->nhm_include_cca = 2;
	environment->clm_nav = environment->clm_rssi = true;
	environment->nhm_nav = environment->nhm_rssi = true;
	environment->clm_rssi_threshold = environment->nhm_rssi_threshold = 0;
	for (i = 0; i < 5; i++) {
		environment->application[i] = 0;
		environment->monitor_time[i] = 0;
	}
	for (i = 0; i < 11; i++) {
		u8 value = thresholds[i] * 2;

		/* Actual C branch sets last encoded threshold218; XB keeps120. */
		if (chip == RTL_AX_8852C && i == 10)
			value = 218;
		environment->nhm_threshold[i] = environment->fahm_threshold[i] = value;
	}
	for (i = 0; i < ARRAY_SIZE(rtl_ax_environment_start); i++) {
		const struct rtl_ax_environment_write *write = &rtl_ax_environment_start[i];

		rtl_ax_update32(io, 0x10000 | write->address, write->mask, write->value);
	}
	/* Threshold arrays remain host state here, as in the actual initializers.
	 * Setting monitor parameters, triggering and report collection follow. */
	environment->previous_false_alarms = 0;
	environment->background_started = environment->nhm_ready = false;
	environment->nhm_period = 59375; /* ccx_ms_2_period_unit(1900):32us. */
	environment->nhm_idle_ratio = environment->nhm_noise_ratio = 0;
	environment->samples = environment->not_ready = 0;
	environment->initialized = true;
}

/* Quantized power-domain mean from ext_loss_avg_update_8852c@2e8028.
 * This is the composition of the factory db/linear tables, not an arithmetic
 * mean of the two calibrated RX paths. Inputs/outputs use its signed format. */
static inline s8 rtl_ax_environment_loss(s8 a, s8 b)
{
	static const u8 offset[] = {
		0,1,1,1,1,3,3,5,5,6,7,8,9,10,11,12,
		13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,
		29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,
		45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,
		61,62,63,64,65,66,57,68,69,71,71,72,73,74,75,76,
		77,78,79,80,81,82,83,84,85,86,87,88,89,90,91,92,93,
	};
	unsigned int difference = a > b ? a - b : b - a;
	s8 average = (a < b ? a : b) + offset[difference > 96 ? 96 : difference];

	return (average + 1) / 2; /* ccx_ext_loss_update@2a5ba4. */
}

static inline u16 rtl_ax_environment_ratio(u16 count, u16 period)
{
	unsigned int ratio;

	if (count == period) return 100;
	ratio = (count * 100U + period / 2) / period;
	return ratio == 100 ? 99 : ratio;
}

/* Real NHM bins and TX/CCA/idle counters, nhm_get_result@2a66a4 and
 * nhm_get_utility@2a6138. The 8852C's last raw bin belongs in bin10. */
static inline bool rtl_ax_environment_sample(const struct rtl_ax_io *io,
	struct rtl_ax_environment *env, unsigned int bandwidth)
{
	u16 bins[12], total = 0, noise;
	u8 weight[12];
	unsigned int i, first = 255, value;
	unsigned int limit = bandwidth == 3 || bandwidth == 4 ? 70 : 60;

	env->nhm_ready = !!(io->read32(io->ctx, 0x11aa4) & BIT(16));
	if (!env->nhm_ready) {
		env->not_ready++;
		return false;
	}
	for (i = 0; i < 6; i++) {
		value = io->read32(io->ctx, 0x11a88 + i * 4);
		env->nhm_bins[i * 2] = value;
		env->nhm_bins[i * 2 + 1] = value >> 16;
	}
	for (i = 0; i < 12; i++) {
		bins[i] = env->nhm_bins[i];
		total += bins[i];
		weight[i] = !i ? env->nhm_threshold[0] - 2 : i == 11 ?
			env->nhm_threshold[9] + 2 :
			(env->nhm_threshold[i - 1] + env->nhm_threshold[i]) / 2;
	}
	weight[10] = weight[11];
	bins[10] += bins[11];
	bins[11] = 0;
	for (i = 0; i < 12; i++)
		if (rtl_ax_environment_ratio(bins[i], env->nhm_period)) {
			first = i;
			break;
		}
	noise = total;
	for (i = 0; i < 12; i++)
		if (i < first + 3 && weight[i] <= limit) noise -= bins[i];
	value = io->read32(io->ctx, 0x11aa0);
	env->nhm_cca = value;
	env->nhm_tx = value >> 16;
	env->nhm_idle = io->read32(io->ctx, 0x11aa4);
	env->nhm_idle_ratio = rtl_ax_environment_ratio(env->nhm_idle, env->nhm_period);
	env->nhm_noise_ratio = rtl_ax_environment_ratio(noise, env->nhm_period);
	env->samples++;
	return true;
}

static inline void rtl_ax_environment_trigger(const struct rtl_ax_io *io,
	struct rtl_ax_environment *env, s8 loss)
{
	unsigned int i, address, shift;

	/* Background nhm_set@2a6e78:1900ms,32us units, include CCA mode0,
	 * application1, no NAV/RSSI filter, factory thresholds minus RX loss. */
	if (!env->background_started) {
		rtl_ax_update32(io, 0x10c08, 0xffff, env->nhm_period);
		rtl_ax_update32(io, 0x10c08, 0x30000, 3U << 16);
		rtl_ax_update32(io, 0x10c08, BIT(19), 0);
		rtl_ax_update32(io, 0x10c80, BIT(1) | BIT(0), 0);
		env->nhm_include_cca = 0;
		env->nhm_nav = env->nhm_rssi = false;
	}
	if (!env->background_started || env->external_loss != loss) {
		for (i = 0; i < 11; i++) {
			address = i ? 0x10c0c + ((i - 1) / 4) * 4 : 0x10c08;
			shift = i ? ((i - 1) % 4) * 8 : 24;
			/* Factory keeps the terminal encoded threshold218 unchanged. */
			rtl_ax_update32(io, address, 255U << shift,
				(u32)(u8)(env->nhm_threshold[i] - (i == 10 ? 0 : loss)) << shift);
		}
		env->external_loss = loss;
	}
	rtl_ax_update32(io, 0x10c08, BIT(18), 0);
	rtl_ax_update32(io, 0x10c08, BIT(18), BIT(18));
	env->background_started = true;
}

/* Five-GHz OFDM false alarms from ofdm_cnt_statistics@2cf3a8;
 * statistics' BB counter reset belongs to the same two-second window. */
static inline void rtl_ax_environment_false_alarms(const struct rtl_ax_io *io,
	struct rtl_ax_environment *env)
{
	env->false_alarms = io->read32(io->ctx, 0x11a08) >> 16;
	rtl_ax_update32(io, 0x12340, BIT(7), 0);
	rtl_ax_update32(io, 0x12340, BIT(7), BIT(7));
	rtl_ax_update32(io, 0x10730, BIT(1), BIT(1));
	/* cnt_reg_reset also calls mp_reset_cnt. This device has one PHY;
	 * its rising/falling bit0 pulse clears the OFDM counters. */
	rtl_ax_update32(io, 0x10730, BIT(0), BIT(0));
	rtl_ax_update32(io, 0x10730, BIT(0), 0);
}
#endif
