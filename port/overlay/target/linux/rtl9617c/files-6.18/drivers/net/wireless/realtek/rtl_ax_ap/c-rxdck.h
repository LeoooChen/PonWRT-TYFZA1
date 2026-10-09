/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_C_RXDCK_H
#define RTL_AX_AP_C_RXDCK_H
#include "rxdck.h"

static inline int rtl_ax_c_rxdck_toggle(const struct rtl_ax_io *io,
	unsigned int path, unsigned int pass, struct rtl_ax_rxdck *state)
{
	u32 value;
	unsigned int i;
	int ret;

	/* Actual toggle_C@30c150: RF92[0]0→1,RF93[5] completion.
	 * Read immediately, max501 observations/500 delays2us,then clear. */
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x92, 1, 0);
	if (ret)
		return ret;
	ret = rtl_ax_phy_rf_write_masked(io, path, 0x92, 1, 1);
	if (ret)
		return ret;
	for (i = 0; i < 501; i++) {
		ret = rtl_ax_phy_rf_read(io, path, 0x93, &value);
		if (ret)
			return ret;
		state->observations[path][pass]++;
		state->done[path][pass] = !!(value & BIT(5));
		if (state->done[path][pass] || i == 500)
			break;
		io->delay_us(io->ctx, 2);
	}
	return rtl_ax_phy_rf_write_masked(io, path, 0x92, 1, 0);
}

static inline int rtl_ax_c_rxdck_rewrite(const struct rtl_ax_io *io,
	unsigned int path, unsigned int gain, u8 offset_i, u8 offset_q)
{
	static const u16 reg[] = { 0xec, 0xf0, 0x92, 0x33, 0x3f, 0x3f, 0x92, 0xf0, 0xec };
	static const u32 mask[] = { BIT(19), BIT(16), 2, 255, 0xfffff, 0xfffff, 2, BIT(16), BIT(19) };
	u32 packed = (offset_i >> 1) | (offset_q >> 1) << 6;
	u32 value[] = { 1, 1, 1, gain, packed, packed, 0, 0, 0 };
	unsigned int i;
	int ret;

	/* Actual value_rewrite@30b854 writes LUT3f twice, then closes gates. */
	for (i = 0; i < ARRAY_SIZE(reg); i++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, reg[i], mask[i], value[i]);
		if (ret)
			return ret;
	}
	return 0;
}

static inline int rtl_ax_c_rxdck_read_gain(const struct rtl_ax_io *io,
	unsigned int path, unsigned int gain, u8 result[2])
{
	u32 value;
	int ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0x7c00, gain);

	if (ret)
		return ret;
	ret = rtl_ax_phy_rf_read(io, path, 0x92, &value);
	if (ret)
		return ret;
	result[0] = (value >> 9) & 127;
	ret = rtl_ax_phy_rf_read(io, path, 0x93, &value);
	if (ret)
		return ret;
	result[1] = (value >> 9) & 127;
	return 0;
}

static inline int rtl_ax_c_rxdck_check_gain(const struct rtl_ax_io *io,
	unsigned int path, unsigned int gain, u8 reference[2], u8 observed[2],
	struct rtl_ax_rxdck *state)
{
	bool replace_i = abs((int)reference[0] - observed[0]) > 9;
	bool replace_q = abs((int)reference[1] - observed[1]) > 9;
	int ret;

	/* Exact check_C@30bd3c: independently replace only the offsets whose
	 * absolute difference exceeds9, using the earlier gain pair. */
	if (!replace_i && !replace_q)
		return 0;
	ret = rtl_ax_c_rxdck_rewrite(io, path, gain,
		replace_i ? reference[0] : observed[0], replace_q ? reference[1] : observed[1]);
	if (!ret)
		state->corrections[path]++;
	return ret;
}

static inline int rtl_ax_c_rxdck_offsets(const struct rtl_ax_io *io,
	unsigned int path, struct rtl_ax_rxdck *state)
{
	static const u8 first[2] = { 0, 16 }, second[2] = { 12, 28 };
	unsigned int group, index, slot;
	int ret;

	/* Actual read_offset@30be50 reads first,first+1,second,second+1;
	 * checks second,then rereads second+1 and checks that separately. */
	for (group = 0; group < 2; group++) {
		slot = group * 4;
		for (index = 0; index < 4; index++) {
			u8 gain = index < 2 ? first[group] + index : second[group] + index - 2;

			ret = rtl_ax_c_rxdck_read_gain(io, path, gain, state->offsets[path][slot + index]);
			if (ret)
				return ret;
		}
		ret = rtl_ax_c_rxdck_check_gain(io, path, second[group],
			state->offsets[path][slot], state->offsets[path][slot + 2], state);
		if (ret)
			return ret;
		ret = rtl_ax_c_rxdck_read_gain(io, path, second[group] + 1, state->offsets[path][slot + 3]);
		if (ret)
			return ret;
		ret = rtl_ax_c_rxdck_check_gain(io, path, second[group] + 1,
			state->offsets[path][slot + 1], state->offsets[path][slot + 3], state);
		if (ret)
			return ret;
	}
	return 0;
}

static inline int rtl_ax_c_rxdck_start(const struct rtl_ax_io *io,
	struct rtl_ax_rxdck *state, bool tssi_running)
{
	u32 value, rf0 = 0;
	unsigned int path, i;
	bool automatic;
	int ret;

	/* Original RX_DCK initializer@2f69ec enables the extra reK from
	 * pathA RF95[3]. Normal MP=0,DBCC=0 path of rx_dck@30c310. */
	ret = rtl_ax_phy_rf_read(io, 0, 0x95, &value);
	if (ret)
		return ret;
	state->rek_enabled = !!(value & BIT(3));
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_read(io, path, 5, &value);
		if (ret)
			return ret;
		automatic = !!(value & 1);
		if (!automatic) {
			ret = rtl_ax_phy_rf_read(io, path, 0, &rf0);
			if (ret)
				return ret;
		}
		if (tssi_running)
			rtl_ax_channel_field(io, 0x5818 + path * 0x2000, BIT(30), 1);
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 0);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xf0000, 3);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0, 2, 0);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x93, 15, 0);
		if (ret)
			return ret;
		ret = rtl_ax_c_rxdck_toggle(io, path, 0, state);
		if (ret)
			return ret;
		if (state->rek_enabled) {
			ret = rtl_ax_phy_rf_read(io, path, 0x92, &value);
			if (ret)
				return ret;
			value = (value >> 5) & 7;
			if (value > 1) {
				ret = rtl_ax_phy_rf_write_masked(io, path, 0x8f, 0xe00, value);
				if (ret)
					return ret;
				ret = rtl_ax_c_rxdck_toggle(io, path, 1, state);
				if (ret)
					return ret;
				ret = rtl_ax_phy_rf_write_masked(io, path, 0x8f, 0xe00, 1);
				if (ret)
					return ret;
			}
		}
		ret = rtl_ax_c_rxdck_offsets(io, path, state);
		if (ret)
			return ret;
		for (i = 0; i < 3; i++) {
			ret = rtl_ax_phy_rf_write_masked(io, path, 0x42, BIT(19), i == 1 ? 0 : 1);
			if (ret)
				return ret;
		}
		io->delay_us(io->ctx, 200);
		ret = rtl_ax_phy_rf_read(io, path, 0x42, &value);
		if (ret)
			return ret;
		state->thermal[path] = (value >> 1) & 63;
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 1);
		if (ret)
			return ret;
		if (!automatic) {
			ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xfffff, rf0);
			if (ret)
				return ret;
		}
		if (tssi_running)
			rtl_ax_channel_field(io, 0x5818 + path * 0x2000, BIT(30), 0);
		ret = rtl_ax_phy_rf_read(io, path, 0x92, &state->rf_result[path][0]);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_read(io, path, 0x93, &state->rf_result[path][1]);
		if (ret)
			return ret;
	}
	state->ready = true;
	return 0;
}
#endif
