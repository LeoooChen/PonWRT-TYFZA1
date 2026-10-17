/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_XB_IQK_H
#define RTL_AX_AP_XB_IQK_H
#include "channel.h"

/* Actual TYFZA1 XB host IQK, normal SCC/2G/20/40MHz, hardwareCV1.
 * rf_iqk_hwspec_XB@.data196b30; generic doiQK@30a91c;
 * chip callbacks3429e4/344204/344144/342cf0/343e44/343f3c. */
static const u32 rtl_ax_xb_iqk_bb_regs[] = {
	0x2344, 0xc0d4, 0xc0d8, 0xc0c4, 0xc0e8, 0xc0ec,
	0xc1d4, 0xc1d8, 0xc1c4, 0xc1e8, 0xc1ec,
};
static const u32 rtl_ax_xb_iqk_rf_regs[] = { 0xdc, 0xef, 0xde, 0, 0x1e, 5, 0x10005 };

struct rtl_ax_xb_iqk {
	bool ready, shot_fail[2][16], lok_range_fail[2], tx_fail[2], rx_fail[2];
	u8 channel, bandwidth, times, thermal[2];
	u32 failures, lok_idac[2], lok_vbuffer[2], tx_result[2], rx_result[2];
	u32 iqc_enable[2], nb_tx[2], nb_rx[2], status;
	u32 bb_saved[2][11], bb_restored[2][11], rf_saved[2][7], rf_restored[2][7];
	u16 observations[2][16], first_status[2][16], second_status[2][16];
	u8 shots[2];
};

struct rtl_ax_iqk_rf_entry { u32 address, mask, value; };

static inline int rtl_ax_iqk_rf_entries(const struct rtl_ax_io *io, unsigned int path,
	const struct rtl_ax_iqk_rf_entry *entries, unsigned int count)
{
	unsigned int i;
	int ret;

	for (i = 0; i < count; i++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, entries[i].address, entries[i].mask, entries[i].value);
		if (ret)
			return ret;
	}
	return 0;
}

static inline bool rtl_ax_xb_iqk_shot(const struct rtl_ax_io *io, unsigned int path,
	u32 command, bool rf_control, struct rtl_ax_xb_iqk *state)
{
	unsigned int slot = state->shots[path]++, delays = 0;
	bool fail = false;
	u16 value;

	/* Actual one_shot@341c48: command8000, first bff8 byte55.
	 * A successful first poll jumps directly to8008[26]. The original
	 * timeout branch retains its first count for the80fc/200 comparison. */
	if (rf_control)
		rtl_ax_channel_field(io, 0x5670 + path * 0x2000, 2, 1);
	io->write32(io->ctx, 0x18000, (command | BIT(4 + path)) + 1);
	io->delay_us(io->ctx, 1);
	for (;;) {
		value = io->read32(io->ctx, 0x1bff8) & 255;
		state->observations[path][slot]++;
		if (value == 0x55) {
			io->delay_us(io->ctx, 1);
			break;
		}
		io->delay_us(io->ctx, 1);
		if (++delays > 8200) {
			fail = true;
			break;
		}
	}
	state->first_status[path][slot] = value;
	if (fail) {
		value = io->read32(io->ctx, 0x180fc) & 65535;
		state->second_status[path][slot] = value;
		io->delay_us(io->ctx, 1);
		if (value == 0x8000)
			fail = false;
	}
	io->delay_us(io->ctx, 10);
	if (!fail)
		fail = !!(io->read32(io->ctx, 0x18008) & BIT(26));
	rtl_ax_channel_field(io, 0x8010, 255, 0);
	rtl_ax_channel_field(io, 0x5670 + path * 0x2000, 2, 0);
	state->shot_fail[path][slot] = fail;
	return fail;
}

static inline void rtl_ax_xb_iqk_rxclock(const struct rtl_ax_io *io, unsigned int path,
	unsigned int selector)
{
	/* Actual rxck_force_XB@33e138, CV1 selectors3/2 use .data196c38/
	 * 196c98 respectively. These six values differ from older SDK tables. */
	static const u32 parameters[2][6] = { { 9, 0, 0, 3, 15, 10 }, { 8, 2, 2, 4, 15, 10 } };
	const u32 *values = parameters[selector == 2];
	u32 clock = 0x12a0 + path * 0x2000, base = 0xc000 + path * 0x100;

	rtl_ax_channel_field(io, clock, BIT(19), 0);
	rtl_ax_channel_field(io, clock, 0x70000, selector);
	rtl_ax_channel_field(io, clock, BIT(19), 1);
	rtl_ax_channel_field(io, base + 0xd4, 0x780, values[0]);
	rtl_ax_channel_field(io, base + 0xd4, 0x7800, values[1]);
	rtl_ax_channel_field(io, base + 0xd4, 0x0c000000, values[2]);
	rtl_ax_channel_field(io, base + 0xd8, 0x1e0, values[3]);
	rtl_ax_channel_field(io, base + 0xc4, 0x3e0000, values[4]);
	rtl_ax_channel_field(io, base + 0xe8, 0xffff0000, values[5]);
}

static inline int rtl_ax_xb_iqk_prepare(const struct rtl_ax_io *io, unsigned int path)
{
	u32 clock = 0x12a0 + path * 0x2000, control = 0x5670 + path * 0x2000;
	int ret = rtl_ax_phy_rf_write_masked(io, path, 0x10005, 1, 0);

	if (ret)
		return ret;
	rtl_ax_channel_field(io, 0x20fc, BIT(16 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(20 + path), 0);
	rtl_ax_channel_field(io, 0x20fc, BIT(24 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(28 + path), 0);
	rtl_ax_channel_field(io, path ? 0x4a9c : 0x4730, BIT(31), 0);
	io->write32(io->ctx, control | 0x10000, 0xf801fffd);
	rtl_ax_channel_field(io, control, BIT(14), 1);
	rtl_ax_channel_field(io, control, BIT(31), 1);
	/* Actual txck_force@33e0c4: clear force,selector7,enable force. */
	rtl_ax_channel_field(io, clock, BIT(15), 0);
	rtl_ax_channel_field(io, clock, 0x7000, 7);
	rtl_ax_channel_field(io, clock, BIT(15), 1);
	rtl_ax_channel_field(io, control, BIT(13), 1);
	rtl_ax_xb_iqk_rxclock(io, path, 3);
	rtl_ax_channel_field(io, control, 0x60000000, 2);
	rtl_ax_channel_field(io, 0xc0ec + path * 0x100, 0x6000, 0);
	rtl_ax_channel_field(io, 0x12b8 + path * 0x2000, BIT(30), 1);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 31);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 19);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 1);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 0x41);
	rtl_ax_channel_field(io, 0x20fc, BIT(20 + path), 1);
	rtl_ax_channel_field(io, 0x20fc, BIT(28 + path), 1);
	ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 0);
	if (ret)
		return ret;
	io->write32(io->ctx, 0x18008, 0x80);
	io->write32(io->ctx, 0x18088, 0x81ff010a);
	return 0;
}

static inline int rtl_ax_xb_iqk_lok(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_xb_iqk *state)
{
	static const struct rtl_ax_iqk_rf_entry setting[] = {
		{0x51,0x80000,0}, {0x51,0x800,0}, {0x52,0x800,1},
		{0x55,0x1f,0}, {0xef,4,1}, {0x33,255,0}, {0,0xfffff,0x403e1},
	};
	static const u32 commands[] = { 0x108, 0x308, 0x208, 0x308 };
	unsigned int i;
	u32 value;
	int ret = rtl_ax_iqk_rf_entries(io, path, setting, ARRAY_SIZE(setting));

	if (ret)
		return ret;
	io->delay_us(io->ctx, 10);
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x11, 3, 0);
	if (ret) return ret;
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x11, 0x70, 6);
	if (ret) return ret;
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x11, 0x1f000, 6);
	if (ret) return ret;
	rtl_ax_channel_field(io, 0x802c, 0xfff, 0x21);
	for (i = 0; i < 4; i++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x11, 0x1f000, i & 1 ? 18 : 6);
		if (ret) return ret;
		io->write32(io->ctx, (0x81cc + path * 0x100) | 0x10000, i & 1 ? 27 : 9);
		rtl_ax_xb_iqk_shot(io, path, commands[i], true, state);
	}
	ret = rtl_ax_phy_rf_read(io, path, 0x58, &value);
	if (ret) return ret;
	state->lok_idac[path] = value;
	state->lok_range_fail[path] = ((value >> 15) & 31) < 2 || ((value >> 15) & 31) > 29 ||
		((value >> 10) & 31) < 2 || ((value >> 10) & 31) > 29;
	ret = rtl_ax_phy_rf_read(io, path, 0x0a, &value);
	if (ret) return ret;
	state->lok_vbuffer[path] = value;
	state->lok_range_fail[path] |= ((value >> 14) & 63) < 2 || ((value >> 14) & 63) > 61 ||
		((value >> 4) & 63) < 2 || ((value >> 4) & 63) > 61;
	return 0;
}

static inline int rtl_ax_xb_iqk_txk(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_xb_iqk *state)
{
	static const u32 track[] = { 4, 4, 6, 6 }, gain[] = { 20, 20, 8, 14 };
	unsigned int group;
	int ret;

	state->tx_fail[path] = false;
	for (group = 1; group < 4; group++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x11, 3, 0);
		if (ret) return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x11, 0x70, track[group]);
		if (ret) return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x11, 0x1f000, gain[group]);
		if (ret) return ret;
		rtl_ax_channel_field(io, 0x81cc + path * 0x100, 63, 18);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 0x100, 1);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 8, 1);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 4, 0);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 3, group);
		rtl_ax_channel_field(io, 0x8010, 255, 0);
		state->tx_fail[path] |= rtl_ax_xb_iqk_shot(io, path, 0x808 + (state->bandwidth << 8), false, state);
		rtl_ax_channel_field(io, 0x9fe0, BIT(8 + group + path * 4), state->shot_fail[path][3 + group]);
	}
	state->nb_tx[path] = state->tx_fail[path] ? 0x40000002 : 0x40000000;
	return 0;
}

static inline int rtl_ax_xb_iqk_rxk(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_xb_iqk *state)
{
	static const u32 gains[] = { 0x150, 0x158, 0x2d0, 0x2d8 };
	unsigned int group;
	u32 value, control = 0x5670 + path * 0x2000;
	int ret;

	rtl_ax_channel_field(io, 0x20fc, 0xffff0000, path ? 0x202 : 0x101);
	rtl_ax_channel_field(io, control, 0x2000, 1);
	rtl_ax_xb_iqk_rxclock(io, path, 2);
	rtl_ax_channel_field(io, control, 0x60000000, 0);
	rtl_ax_channel_field(io, 0xc0ec + path * 0x100, 0x6000, 2);
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x8f, 0x1000, 1);
	if (ret) return ret;
	rtl_ax_channel_field(io, 0x12b8 + path * 0x2000, BIT(30), 1);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 15);
	rtl_ax_channel_field(io, 0x30c, 0xff000000, 3);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 1);
	rtl_ax_channel_field(io, 0x32c, 0xffff0000, 0x41);
	rtl_ax_channel_field(io, 0x20fc, 0xffff0000, path ? 0x2202 : 0x1101);
	if (path) {
		ret = rtl_ax_phy_rf_write_masked(io, 1, 0xdc, 0x300, 3);
		if (ret) return ret;
		ret = rtl_ax_phy_rf_read(io, 1, 0x17, &value);
		if (ret) return ret;
		ret = rtl_ax_phy_rf_write_masked(io, 1, 0x1f, 0x30000, (value >> 10) & 3);
		if (ret) return ret;
		ret = rtl_ax_phy_rf_write_masked(io, 1, 0x1f, 0x3ff, value & 0x3ff);
		if (ret) return ret;
	}
	ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xf0000, 12);
	if (ret) return ret;
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x84, 0xf0000, 10);
	if (ret) return ret;
	io->delay_us(io->ctx, 10);
	state->rx_fail[path] = false;
	for (group = 0; group < 4; group++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0x3ff0, gains[group]);
		if (ret) return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x83, 0xf000, 0);
		if (ret) return ret;
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 0x100, 1);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 8, 0);
		rtl_ax_channel_field(io, 0x8154 + path * 0x100, 7, group);
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x1e, 0xfffff, 0x80013);
		if (ret) return ret;
		io->delay_us(io->ctx, 100);
		io->delay_us(io->ctx, 100);
		ret = rtl_ax_phy_rf_read(io, path, 0, &value);
		if (ret) return ret;
		rtl_ax_channel_field(io, 0x8024, 0xfffff, value);
		rtl_ax_channel_field(io, 0x8044, 0xe000, group);
		rtl_ax_channel_field(io, 0x802c, 0x0fff0000, 0x11);
		rtl_ax_xb_iqk_shot(io, path, 0x708, true, state);
		io->delay_us(io->ctx, 10);
		state->rx_fail[path] |= rtl_ax_xb_iqk_shot(io, path, 0xc08 + (state->bandwidth << 8), true, state);
		rtl_ax_channel_field(io, 0x9fe0, BIT(16 + group + path * 4), state->shot_fail[path][8 + group * 2]);
	}
	if (path) {
		ret = rtl_ax_phy_rf_write_masked(io, 1, 0xdc, 0x300, 0);
		if (ret) return ret;
	}
	state->nb_rx[path] = state->rx_fail[path] ? 0x40000002 : 0x40000000;
	return 0;
}

static inline int rtl_ax_xb_iqk_restore(const struct rtl_ax_io *io, unsigned int path,
	struct rtl_ax_xb_iqk *state)
{
	unsigned int i;
	int ret, result = 0;

	io->write32(io->ctx, (0x8138 + path * 0x100) | 0x10000, state->nb_tx[path]);
	io->write32(io->ctx, (0x813c + path * 0x100) | 0x10000, state->nb_rx[path]);
	rtl_ax_xb_iqk_shot(io, path, 0x1208, false, state);
	rtl_ax_channel_field(io, 0x8010, 255, 0);
	io->write32(io->ctx, 0x18008, 0);
	io->write32(io->ctx, 0x18088, 0x80000000);
	ret = rtl_ax_phy_rf_write_masked(io, path, 0xef, 4, 0);
	if (ret) result = ret;
	ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xf0000, 3);
	if (ret) result = ret;
	ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 1);
	if (ret) result = ret;
	/* Actual AFE restore@343f3c, then generic BB/RF backups, thenRF5. */
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
	for (i = 0; i < ARRAY_SIZE(rtl_ax_xb_iqk_bb_regs); i++) {
		io->write32(io->ctx, rtl_ax_xb_iqk_bb_regs[i] | 0x10000, state->bb_saved[path][i]);
		state->bb_restored[path][i] = io->read32(io->ctx, rtl_ax_xb_iqk_bb_regs[i] | 0x10000);
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

static inline int rtl_ax_xb_iqk_start(const struct rtl_ax_io *io, unsigned int center,
	unsigned int bandwidth, struct rtl_ax_xb_iqk *state)
{
	unsigned int path, i;
	u32 value;
	int ret, restore_ret;

	state->ready = false;
	state->channel = center;
	state->bandwidth = bandwidth;
	memset(state->shots, 0, sizeof(state->shots));
	memset(state->observations, 0, sizeof(state->observations));
	memset(state->first_status, 0, sizeof(state->first_status));
	memset(state->second_status, 0, sizeof(state->second_status));
	io->write32(io->ctx, 0x19fe0, 0); /* iqk_init_XB@3448c4 every trigger. */
	for (path = 0; path < 2; path++) {
		/* Original get_ch_info metadata; normal SCC/2G. */
		ret = rtl_ax_phy_rf_read(io, path, 0x18, &value);
		if (ret) return ret;
		rtl_ax_channel_field(io, 0x9fe0, 0xff000000, 6);
		rtl_ax_channel_field(io, 0x9fe4, 0xfU << (path * 16), 0);
		rtl_ax_channel_field(io, 0x9fe4, 0xf0U << (path * 16), bandwidth);
		rtl_ax_channel_field(io, 0x9fe4, 0xff00U << (path * 16), center);
		rtl_ax_channel_field(io, 0x9fe8, 255, 10);
		for (i = 0; i < ARRAY_SIZE(rtl_ax_xb_iqk_bb_regs); i++)
			state->bb_saved[path][i] = io->read32(io->ctx, rtl_ax_xb_iqk_bb_regs[i] | 0x10000);
		for (i = 0; i < ARRAY_SIZE(rtl_ax_xb_iqk_rf_regs); i++) {
			ret = rtl_ax_phy_rf_read(io, path, rtl_ax_xb_iqk_rf_regs[i], &state->rf_saved[path][i]);
			if (ret) return ret;
		}
		ret = rtl_ax_xb_iqk_prepare(io, path);
		if (!ret) ret = rtl_ax_xb_iqk_lok(io, path, state);
		if (!ret) ret = rtl_ax_xb_iqk_txk(io, path, state);
		if (!ret) ret = rtl_ax_xb_iqk_rxk(io, path, state);
		if (!ret) {
			ret = rtl_ax_phy_rf_write_masked(io, path, 0x42, BIT(19), 1);
			if (!ret) ret = rtl_ax_phy_rf_write_masked(io, path, 0x42, BIT(19), 0);
			if (!ret) ret = rtl_ax_phy_rf_write_masked(io, path, 0x42, BIT(19), 1);
			io->delay_us(io->ctx, 200);
			if (!ret) ret = rtl_ax_phy_rf_read(io, path, 0x42, &value);
			if (!ret) state->thermal[path] = (value >> 1) & 63;
		}
		if (!ret) {
			rtl_ax_channel_field(io, 0x9fe0, BIT(path * 4), state->shot_fail[path][0]);
			rtl_ax_channel_field(io, 0x9fe0, BIT(1 + path * 4), state->shot_fail[path][2]);
			rtl_ax_channel_field(io, 0x9fe0, BIT(2 + path * 4), state->tx_fail[path]);
			rtl_ax_channel_field(io, 0x9fe0, BIT(3 + path * 4), state->rx_fail[path]);
			state->iqc_enable[path] = io->read32(io->ctx, (0x8124 + path * 0x100) | 0x10000);
			state->tx_result[path] = io->read32(io->ctx, (0x8138 + path * 0x100) | 0x10000);
			state->rx_result[path] = io->read32(io->ctx, (0x813c + path * 0x100) | 0x10000);
			rtl_ax_channel_field(io, 0x9fe8, 0xff00, state->times);
			if ((io->read32(io->ctx, 0x19fe0) >> (path * 4)) & 15)
				state->failures++;
			rtl_ax_channel_field(io, 0x9fe8, 0xff0000U << (path * 4), state->failures);
		}
		restore_ret = rtl_ax_xb_iqk_restore(io, path, state);
		state->times++;
		if (ret || restore_ret)
			return ret ? ret : restore_ret;
	}
	state->status = io->read32(io->ctx, 0x19fe0);
	state->ready = true;
	return 0;
}
#endif
