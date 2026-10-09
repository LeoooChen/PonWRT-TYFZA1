/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RXDCK_H
#define RTL_AX_AP_RXDCK_H
#include "channel.h"

struct rtl_ax_rxdck {
	bool ready, done[2][2];
	u16 observations[2][2];
	u32 saved[2][7], restored[2][7], rf_result[2][2];
	u8 offsets[2][8][2], thermal[2], corrections[2];
	bool rek_enabled;
};

static inline void rtl_ax_xb_rxdck_afe(const struct rtl_ax_io *io,
	unsigned int path, struct rtl_ax_rxdck *state)
{
	unsigned int base = path ? 0xc100 : 0xc000;
	unsigned int clock = path ? 0x32a0 : 0x12a0;
	unsigned int gate = path ? 0x32b8 : 0x12b8;
	u32 regs[7] = { clock, base + 0xd4, base + 0xd8, base + 0xc4,
		base + 0xe8, 0x8034, 0x80d4 };
	unsigned int i, pass;

	/* Actual halrf_set_rx_dck_XB@33be54,is_afe=1. Seven backups,
	 * CV1 rxclock selector3 table .data+196c38={9,0,0,3,15,10}. */
	for (i = 0; i < 7; i++)
		state->saved[path][i] = io->read32(io->ctx, regs[i] | 0x10000);
	rtl_ax_channel_field(io, gate, BIT(30), 1);
	rtl_ax_channel_field(io, clock, BIT(19), 0);
	rtl_ax_channel_field(io, clock, 0x70000, 3);
	rtl_ax_channel_field(io, clock, BIT(19), 1);
	rtl_ax_channel_field(io, base + 0xd4, 0x780, 9);
	rtl_ax_channel_field(io, base + 0xd4, 0x7800, 0);
	rtl_ax_channel_field(io, base + 0xd4, 0x0c000000, 0);
	rtl_ax_channel_field(io, base + 0xd8, 0x1e0, 3);
	rtl_ax_channel_field(io, base + 0xc4, 0x3e0000, 15);
	rtl_ax_channel_field(io, base + 0xe8, 0xffff0000, 10);
	rtl_ax_channel_field(io, base + 0xf8, 0x30000000, 3);
	rtl_ax_channel_field(io, base + 0xf4, 0xc0, 3);
	rtl_ax_channel_field(io, 0x032c, 0x70000, 1);
	rtl_ax_channel_field(io, 0x030c, 0x0f000000, 15);
	rtl_ax_channel_field(io, 0x030c, 0x0f000000, 3);
	rtl_ax_channel_field(io, 0x032c, BIT(30), 0);
	rtl_ax_channel_field(io, 0x032c, BIT(22), 0);
	rtl_ax_channel_field(io, 0x032c, BIT(22), 1);
	rtl_ax_channel_field(io, 0x5670, BIT(1), 1);
	rtl_ax_channel_field(io, 0x8008, BIT(7), 1);
	rtl_ax_channel_field(io, 0x8034, 0x30, 2);
	rtl_ax_channel_field(io, 0x80d4, 0x700, 1);
	rtl_ax_channel_field(io, 0x81e0, 0x700000, 0);
	rtl_ax_channel_field(io, 0x81e0, BIT(25), 0);
	rtl_ax_channel_field(io, 0x82e0, 0x700000, 1);
	rtl_ax_channel_field(io, 0x82e0, BIT(25), 0);
	rtl_ax_channel_field(io, 0x8000, 6, path);
	for (pass = 0; pass < 2; pass++) {
		u32 command = pass ? 0xfc050001 : 0xfc030001;

		io->write32(io->ctx, 0x180ac, command);
		io->write32(io->ctx, 0x180ac, command & ~BIT(31));
		rtl_ax_channel_field(io, 0x80d4, 0x3f0000, 0x34);
		/* Original first-read poll, max1501 reads/1500 delays2us.
		 * It continues with the second pass and restore on the limit. */
		for (i = 0; i < 1501; i++) {
			state->observations[path][pass]++;
			state->done[path][pass] = !!(io->read32(io->ctx, 0x180fc) & BIT(16));
			if (state->done[path][pass] || i == 1500)
				break;
			io->delay_us(io->ctx, 2);
		}
	}
	/* The actual ELF has no SDK RF8f/5670 toggles between passes or
	 * RF8f restore after them. Preserve this board function's writes. */
	io->write32(io->ctx, 0x18008, 0);
	rtl_ax_channel_field(io, gate, BIT(30), 0);
	for (i = 0; i < 7; i++)
		io->write32(io->ctx, regs[i] | 0x10000, state->saved[path][i]);
	rtl_ax_channel_field(io, 0x5670, BIT(1), 0);
	for (i = 0; i < 7; i++)
		state->restored[path][i] = io->read32(io->ctx, regs[i] | 0x10000);
}

static inline int rtl_ax_xb_rxdck_start(const struct rtl_ax_io *io,
	struct rtl_ax_rxdck *state)
{
	unsigned int path;
	int ret;

	/* Exact rx_dck_XB@33c498,initial is_tssi_mode=0: A→B. It
	 * brackets both paths in8080=4/0 and returnsRF5 to automatic. */
	io->write32(io->ctx, 0x18080, 4);
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 0);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xf0000, 3);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x93, 15, 8);
		if (ret)
			return ret;
		rtl_ax_xb_rxdck_afe(io, path, state);
		/* Actual RX_DCK debug getter reads RF92/93. The unused c400
		 * check function has no call sites in this original host module. */
		ret = rtl_ax_phy_rf_read(io, path, 0x92, &state->rf_result[path][0]);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_read(io, path, 0x93, &state->rf_result[path][1]);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 1);
		if (ret)
			return ret;
	}
	io->write32(io->ctx, 0x18080, 0);
	state->ready = true;
	return 0;
}
#endif
