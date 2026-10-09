/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_REF_POWER_H
#define RTL_AX_AP_REF_POWER_H
#include "channel.h"

struct rtl_ax_ref_power {
	bool ready;
	u16 power_cw, tssi_cw;
	u32 bb_word, mac_readback, bb_readback[4];
};

static inline void rtl_ax_ref_power_start(const struct rtl_ax_io *io,
	struct rtl_ax_ref_power *state)
{
	static const u32 address[4] = { 0x5804, 0x5808, 0x7804, 0x7808 };
	unsigned int i;

	/* Actual ref_power_to_struct_C@320f18/XB@3485c4 RFE54:
	 * path offset0, base code33, TSSI16dBm code300, OFDM/CCK ref0.
	 * mac_write_pwr_ref@21e884 writes two9-bit zero refs inD200.
	 * BB_C@2e75a8/XB@2f1b7c computes264 powerCW and172 TSSI_CW.
	 * Both write the same27-bit OFDM/CCK value for paths A/B. */
	state->power_cw = 33 * 8;
	state->tssi_cw = 300 - 128;
	state->bb_word = (u32)state->tssi_cw << 18 | (u32)state->power_cw << 9;
	rtl_ax_update32(io, 0xd200, 0x0ffffc00, 0);
	state->mac_readback = io->read32(io->ctx, 0xd200);
	for (i = 0; i < ARRAY_SIZE(address); i++) {
		rtl_ax_channel_field(io, address[i], 0x07ffffff, state->bb_word);
		state->bb_readback[i] = io->read32(io->ctx, address[i] | 0x10000);
	}
	state->ready = true;
}
#endif
