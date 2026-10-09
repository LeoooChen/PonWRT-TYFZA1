/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_DACK_H
#define RTL_AX_AP_DACK_H
#include "power.h"
#include "phy.h"
#include "channel.h"

struct rtl_ax_dack {
	bool drck_ready, drck_done;
	u8 drck_code;
	u32 drck_observations;
	bool adc_ready, adc_done[2];
	u16 adc_dc[2][2];
	u32 adc_observations[2];
	bool dac_ready, dac_done[2];
	u8 msbk[2][2][16], dac_dc[2][2];
	u16 bias[2][2];
	u32 dac_observations[2], reload[2][2][5];
	u32 saved_rf0[2];
};

static inline void rtl_ax_dack_drck(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_dack *dack)
{
	unsigned int i;
	u32 value;

	/* Actual dac_cal first invokes drck_C@326200/XB@34c628.
	 * XB selects the RCK source first,C does not make that initial write. */
	if (chip == RTL_AX_8192XB)
		rtl_ax_update32(io, 0x1c0c4, 0, BIT(9));
	rtl_ax_update32(io, 0x1c0c4, 0, BIT(6));
	for (i = 0; i < 10001; i++) {
		value = io->read32(io->ctx, 0x1c0c8);
		dack->drck_observations++;
		dack->drck_done = !!(value & BIT(3));
		if (dack->drck_done)
			break;
		io->delay_us(io->ctx, 1);
	}
	/* Original continues into read/install after the observation limit. */
	rtl_ax_update32(io, 0x1c0c4, BIT(6), 0);
	rtl_ax_update32(io, 0x1c094, 0, BIT(9));
	io->delay_us(io->ctx, 1);
	rtl_ax_update32(io, 0x1c094, BIT(9), 0);
	dack->drck_code = (io->read32(io->ctx, 0x1c0c8) >> 10) & 31;
	rtl_ax_update32(io, 0x1c0c4, BIT(9), 0);
	rtl_ax_update32(io, 0x1c0c4, 31, dack->drck_code);
	dack->drck_ready = true;
}

static inline void rtl_ax_dack_rxclock80(const struct rtl_ax_io *io,
	unsigned int chip, unsigned int path, bool enabled)
{
	unsigned int reg = path ? 0x32a0 : 0x12a0;
	unsigned int base = path ? 0xc100 : 0xc000;

	/* Actual rxck_force_C@30f5d0/XB@33e138: first clear force.
	 * Board hardwareCV1 calibration uses clock selector2 (80MHz).
	 * XB values are .data+196c98,not the newer SDK ADC table. */
	rtl_ax_channel_field(io, reg, BIT(19), 0);
	if (!enabled)
		return;
	rtl_ax_channel_field(io, reg, 0x70000, 2);
	rtl_ax_channel_field(io, reg, BIT(19), 1);
	if (chip == RTL_AX_8852C) {
		rtl_ax_channel_field(io, base + 0xd4, 0x780, 8);
		rtl_ax_channel_field(io, base + 0xe8, 0xffff0000, 9);
		rtl_ax_channel_field(io, base + 0xe4, 0x30, 2);
		rtl_ax_channel_field(io, reg, 0xff800000, 73);
		rtl_ax_channel_field(io, base + 0xec, 0x6000, 0);
		rtl_ax_channel_field(io, base + 0xd4, 0x7800, 2);
		rtl_ax_channel_field(io, base + 0xd4, 0x0c000000, 2);
		rtl_ax_channel_field(io, base + 0xd8, 0x1e0, 8);
		rtl_ax_channel_field(io, base + 0xc4, 0x3e0000, 0);
		rtl_ax_channel_field(io, base + 0xec, 0xff0000, 3);
	} else {
		rtl_ax_channel_field(io, base + 0xd4, 0x780, 8);
		rtl_ax_channel_field(io, base + 0xd4, 0x7800, 2);
		rtl_ax_channel_field(io, base + 0xd4, 0x0c000000, 2);
		rtl_ax_channel_field(io, base + 0xd8, 0x1e0, 4);
		rtl_ax_channel_field(io, base + 0xc4, 0x3e0000, 15);
		rtl_ax_channel_field(io, base + 0xe8, 0xffff0000, 10);
	}
}

static inline int rtl_ax_dack_adc_start(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_dack *dack)
{
	static const u16 manual[4] = { 0xc210, 0xc224, 0xc238, 0xc24c };
	u32 value;
	unsigned int path, i;
	int ret;

	/* dac_cal_C@327b80/XB@34e004 saves RF0 before DRCK and keeps
	 * manual RF control across ADDCK, its reload and both DAC paths. */
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_read(io, path, 0, &dack->saved_rf0[path]);
		if (ret)
			return ret;
	}
	rtl_ax_dack_drck(io, chip, dack);
	rtl_ax_update32(io, 0x1c0f8, 0x30000000, 0);
	rtl_ax_update32(io, 0x1c1f8, 0x30000000, 0);
	for (i = 0; i < 4; i++)
		rtl_ax_channel_field(io, manual[i], 1, 0);
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 0);
		if (ret)
			return ret;
	}
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xfffff, 0x337e1);
		if (ret)
			return ret;
	}
	for (path = 0; path < 2; path++)
		rtl_ax_dack_rxclock80(io, chip, path, true);
	/* Exact ADDCK original functionC@326c54/XB@34d090. Their
	 * actual36 MMIO field writes and result extraction agree. */
	rtl_ax_channel_field(io, 0xc0f4, 0x30, 0);
	rtl_ax_channel_field(io, 0xc1f4, 0x30, 0);
	for (path = 0; path < 2; path++) {
		unsigned int reg = path ? 0xc1f4 : 0xc0f4;
		unsigned int base = path ? 0xc100 : 0xc000;
		unsigned int gate = path ? 0x32b8 : 0x12b8;

		rtl_ax_channel_field(io, gate, BIT(30), 1);
		rtl_ax_channel_field(io, 0x032c, BIT(30), 0);
		rtl_ax_channel_field(io, 0x032c, BIT(22), 0);
		rtl_ax_channel_field(io, 0x032c, BIT(22), 1);
		rtl_ax_channel_field(io, 0x030c, 0x0f000000, 15);
		rtl_ax_channel_field(io, 0x032c, BIT(16), 0);
		rtl_ax_channel_field(io, base + 0xd4, BIT(4), 1);
		rtl_ax_channel_field(io, 0x030c, 0x0f000000, 3);
		rtl_ax_channel_field(io, reg, BIT(11), 1);
		rtl_ax_channel_field(io, reg, BIT(11), 0);
		io->delay_us(io->ctx, 1);
		rtl_ax_channel_field(io, reg, 0x300, 1);
		for (i = 0; i < 10001; i++) {
			value = io->read32(io->ctx, (base + 0xfc) | 0x10000);
			dack->adc_observations[path]++;
			dack->adc_done[path] = !!(value & 1);
			if (dack->adc_done[path])
				break;
			io->delay_us(io->ctx, 1);
		}
		rtl_ax_channel_field(io, base + 0xd4, BIT(4), 0);
		rtl_ax_channel_field(io, 0x032c, BIT(16), 1);
		rtl_ax_channel_field(io, 0x030c, 0x0f000000, 12);
		rtl_ax_channel_field(io, 0x032c, BIT(30), 1);
		rtl_ax_channel_field(io, reg, 0x300, 0);
		/* Original reads the result register twice,masking I and Q. */
		dack->adc_dc[path][0] = (io->read32(io->ctx, (base + 0xfc) | 0x10000) >> 10) & 1023;
		dack->adc_dc[path][1] = io->read32(io->ctx, (base + 0xfc) | 0x10000) & 1023;
		rtl_ax_channel_field(io, gate, BIT(30), 0);
	}
	for (path = 0; path < 2; path++)
		rtl_ax_dack_rxclock80(io, chip, path, false);
	for (path = 0; path < 2; path++) {
		unsigned int reg = path ? 0xc1f8 : 0xc0f8;

		rtl_ax_channel_field(io, reg, 0x0ffc0000, dack->adc_dc[path][0]);
		rtl_ax_channel_field(io, reg, 0x0003ff00, dack->adc_dc[path][1]);
		rtl_ax_channel_field(io, reg, 0x30000000, 3);
	}
	dack->adc_ready = true;
	return 0;
}

static inline void rtl_ax_dack_reload_path(const struct rtl_ax_io *io,
	struct rtl_ax_dack *dack, unsigned int path)
{
	unsigned int index, i, group;
	static const u16 manual[4] = { 0xc004, 0xc024, 0xc104, 0xc124 };

	/* Actual reload_C@326488/XB@34cd30 have the same result layout. */
	for (index = 0; index < 2; index++) {
		unsigned int offset = path * 0x28 + index * 0x14;

		for (i = 0; i < 4; i++)
			rtl_ax_channel_field(io, manual[i], BIT(17), 1);
		for (group = 0; group < 4; group++) {
			u32 value = 0;

			for (i = 0; i < 4; i++)
				value |= (u32)dack->msbk[path][index][12 - group * 4 + i] << (i * 8);
			dack->reload[path][index][group] = value;
			io->write32(io->ctx, 0x1c200 + offset + group * 4, value);
		}
		dack->reload[path][index][4] = (dack->bias[path][index] << 22) |
			(dack->dac_dc[path][index] << 14);
		io->write32(io->ctx, 0x1c210 + offset, dack->reload[path][index][4]);
		rtl_ax_channel_field(io, 0xc210 + offset, 1, 1);
	}
}

static inline int rtl_ax_dack_dac_start(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_dack *dack)
{
	unsigned int path, i;
	int ret;

	/* Actual per-path DACK C@32747c/32767c,XB@34d8fc/34dba8.
	 * C clears force, sets the selector and enables force; XB enables
	 * force before setting the selector. */
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 1, 0xfffff, 0);
		if (ret)
			return ret;
	}
	for (path = 0; path < 2; path++) {
		unsigned int base = path ? 0xc100 : 0xc000;
		unsigned int clock = path ? 0x32a0 : 0x12a0;
		unsigned int gate = path ? 0x32b8 : 0x12b8;

		if (chip == RTL_AX_8852C) {
			rtl_ax_channel_field(io, clock, BIT(15), 0);
			rtl_ax_channel_field(io, clock, 0x7000, 3);
		}
		rtl_ax_channel_field(io, clock, BIT(15), 1);
		if (chip == RTL_AX_8192XB)
			rtl_ax_channel_field(io, clock, 0x7000, 3);
		rtl_ax_channel_field(io, gate, BIT(30), 1);
		rtl_ax_channel_field(io, 0x030c, BIT(28), 1);
		rtl_ax_channel_field(io, 0x032c, BIT(31), 0);
		if (chip == RTL_AX_8852C) {
			rtl_ax_channel_field(io, base + 4, 0xfff00000, 0x30);
			rtl_ax_channel_field(io, base + 0x24, 0xfff00000, 0x30);
		}
		rtl_ax_channel_field(io, base, BIT(17), 0);
		rtl_ax_channel_field(io, base, BIT(17), 1);
		rtl_ax_channel_field(io, base + 4, 1, 1);
		if (chip == RTL_AX_8852C)
			io->delay_us(io->ctx, 1);
		/* Original short-circuit poll of four completion indications. */
		for (i = 0; i < 10001; i++) {
			dack->dac_observations[path]++;
			dack->dac_done[path] = !!(io->read32(io->ctx, (base + 0x40) | 0x10000) & BIT(31));
			if (dack->dac_done[path])
				dack->dac_done[path] = !!(io->read32(io->ctx, (base + 0x64) | 0x10000) & BIT(31));
			if (dack->dac_done[path])
				dack->dac_done[path] = !!(io->read32(io->ctx, (base + 0x5c) | 0x10000) & 4);
			if (dack->dac_done[path])
				dack->dac_done[path] = !!(io->read32(io->ctx, (base + 0x80) | 0x10000) & 4);
			if (dack->dac_done[path])
				break;
			io->delay_us(io->ctx, 1);
		}
		rtl_ax_channel_field(io, base + 4, 1, 0);
		rtl_ax_channel_field(io, clock, BIT(15), 0);
		if (chip == RTL_AX_8192XB)
			rtl_ax_channel_field(io, clock, 0x7000, 7);
		/* Exact backup_s0/s1: 16 indexed entries per I/Q,then bias/DC. */
		rtl_ax_channel_field(io, gate, BIT(30), 1);
		for (i = 0; i < 16; i++) {
			rtl_ax_channel_field(io, base, 0x1e, i);
			dack->msbk[path][0][i] = io->read32(io->ctx, (base + 0x5c) | 0x10000) >> 24;
			rtl_ax_channel_field(io, base + 0x20, 0x1e, i);
			dack->msbk[path][1][i] = io->read32(io->ctx, (base + 0x80) | 0x10000) >> 24;
		}
		dack->bias[path][0] = (io->read32(io->ctx, (base + 0x48) | 0x10000) >> 2) & 1023;
		dack->bias[path][1] = (io->read32(io->ctx, (base + 0x6c) | 0x10000) >> 2) & 1023;
		dack->dac_dc[path][0] = io->read32(io->ctx, (base + 0x60) | 0x10000) >> 24;
		dack->dac_dc[path][1] = io->read32(io->ctx, (base + 0x84) | 0x10000) >> 24;
		rtl_ax_dack_reload_path(io, dack, path);
		rtl_ax_channel_field(io, gate, BIT(30), 0);
	}
	/* Final dac_cal restores RF0 A/B, then returns RF5 A/B to auto. */
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xfffff, dack->saved_rf0[path]);
		if (ret)
			return ret;
	}
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 1);
		if (ret)
			return ret;
	}
	dack->dac_ready = true;
	return 0;
}
#endif
