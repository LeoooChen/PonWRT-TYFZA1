/* SPDX-License-Identifier: GPL-2.0-only */
/* TYFZA1 XB 2.4GHz/20MHz, hardwareCV1 and original cc/f9 ADC trim. */
#ifndef RTL_AX_AP_XB_CHANNEL_H
#define RTL_AX_AP_XB_CHANNEL_H
#include "channel.h"

static inline void rtl_ax_xb_gain2g(const struct rtl_ax_io *io,
	const struct rtl_ax_phy_gain *gain)
{
	/* Original set_gain_error@2f2b50 constants, .rodata+2b9a8+940..a98. */
	static const u16 lna[2][7] = {
		{ 0x4678, 0x4678, 0x467c, 0x467c, 0x467c, 0x467c, 0x4680 },
		{ 0x475c, 0x475c, 0x4760, 0x4760, 0x4760, 0x4760, 0x4764 },
	};
	static const u32 lna_mask[7] = { 0xff0000, 0xff000000, 0xff, 0xff00, 0xff0000, 0xff000000, 0xff };
	static const u16 bypass[2][7] = {
		{ 0x4bb8, 0x4bbc, 0x4bbc, 0x4bbc, 0x4bbc, 0x4bc0, 0x4bc0 },
		{ 0x4c7c, 0x4c80, 0x4c80, 0x4c80, 0x4c80, 0x4c84, 0x4c84 },
	};
	static const u32 bypass_mask[7] = { 0xff000000, 0xff, 0xff00, 0xff0000, 0xff000000, 0xff, 0xff00 };
	static const u16 op1db[2][7] = {
		{ 0x4684, 0x4684, 0x4684, 0x4688, 0x4688, 0x4688, 0x4688 },
		{ 0x4768, 0x4768, 0x4768, 0x476c, 0x476c, 0x476c, 0x476c },
	};
	static const u32 op_mask[7] = { 0xff00, 0xff0000, 0xff000000, 0xff, 0xff00, 0xff0000, 0xff000000 };
	static const u16 tia_lna[2][7] = {
		{ 0x468c, 0x468c, 0x4690, 0x4690, 0x4690, 0x4690, 0x4694 },
		{ 0x4770, 0x4770, 0x4774, 0x4774, 0x4774, 0x4774, 0x4778 },
	};
	unsigned int path, i;
	u32 value;

	for (path = 0; path < 2; path++) {
		for (i = 0; i < 7; i++) {
			rtl_ax_channel_field(io, lna[path][i], lna_mask[i], (u8)gain->lna[0][path][i]);
			rtl_ax_channel_field(io, bypass[path][i], bypass_mask[i], (u8)gain->lna_bypass[0][path][i]);
		}
		rtl_ax_channel_field(io, path ? 0x4764 : 0x4680, 0xff0000, (u8)gain->tia[0][path][0]);
		rtl_ax_channel_field(io, path ? 0x4764 : 0x4680, 0xff000000, (u8)gain->tia[0][path][1]);
		/* Actual RFE54 external-FEM branch. */
		for (i = 0; i < 7; i++) {
			rtl_ax_channel_field(io, op1db[path][i], op_mask[i], (u8)gain->lna_op1db[0][path][i]);
			rtl_ax_channel_field(io, tia_lna[path][i], lna_mask[i], (u8)gain->tia_lna_op1db[0][path][i]);
		}
		rtl_ax_channel_field(io, path ? 0x4778 : 0x4694, 0xff00, (u8)gain->tia_lna_op1db[0][path][7]);
		rtl_ax_channel_field(io, path ? 0x4aa0 : 0x4734, 0xff800000, gain->wb_elna[0][path] & 511);
		rtl_ax_channel_field(io, path ? 0x4aa4 : 0x4738, 0x7f, (gain->wb_elna[0][path] >> 9) & 127);
		value = 0;
		for (i = 0; i < 7; i++)
			value |= gain->wb_lna_tia[0][path][i] << (i * 3);
		rtl_ax_channel_field(io, path ? 0x4a78 : 0x4724, 0x3ffffe00, value);
		value = 0;
		for (i = 0; i < 9; i++)
			value |= gain->wb_lna_tia[0][path][i + 7] << (i * 3);
		rtl_ax_channel_field(io, path ? 0x4a7c : 0x4728, 0x07ffffff, value);
		rtl_ax_channel_field(io, path ? 0x4a68 : 0x46b4, 0xffffffff, gain->gs[0][path][0]);
		rtl_ax_channel_field(io, path ? 0x4a6c : 0x46b8, 0xfff, gain->gs[0][path][1]);
		value = gain->elna[0][path][0] | gain->elna[0][path][1] << 8;
		rtl_ax_channel_field(io, path ? 0x475c : 0x4678, 0xffff, value);
	}
}

static inline void rtl_ax_xb_gain_cal2g(const struct rtl_ax_io *io,
	const struct rtl_ax_phy_inputs *phy, const struct rtl_ax_bb_dm *dm,
	struct rtl_ax_channel *channel)
{
	unsigned int path;
	int offset = phy->gain_offset[0];

	/* ctrl_ch calls hidden/normal for A, then B. Both use PHY0 in SCC. */
	for (path = 0; path < 2; path++) {
		if (dm->hidden_gain_valid)
			rtl_ax_channel_field(io, path ? 0x4c88 : 0x4bc4, 0xff00,
				(u8)(dm->hidden_cs[path][0] * 4));
		if (dm->normal_gain_valid) {
			int normal = -dm->normal_gain[path][1];

			/* Normal CCK calibration writes shared2340 for each path. */
			rtl_ax_channel_field(io, 0x2340, 0x7f00,
				((offset >> 1) - dm->normal_gain[path][0] * 8) & 127);
			channel->external_loss[path] = normal * 4 + (offset >> 2);
			rtl_ax_channel_field(io, path ? 0x4778 : 0x4694, 0xff0000,
				(u8)-channel->external_loss[path]);
			rtl_ax_channel_field(io, 0x4e0c, path ? 0xff0000 : 0xff00, (u8)(normal * 16));
			rtl_ax_channel_field(io, 0x4e2c, path ? 0xff00 : 0xff, (u8)(normal * 16));
		}
	}
}

static inline void rtl_ax_xb_bb2g20(const struct rtl_ax_io *io,
	const struct rtl_ax_phy_inputs *phy, const struct rtl_ax_bb_dm *dm,
	unsigned int center, struct rtl_ax_channel *channel)
{
	static const u32 barker[14] = { 0x1fe4f, 0x1ff5e, 0x2006c, 0x2017b, 0x2028a, 0x20399, 0x204a8,
		0x205b6, 0x206c5, 0x207d4, 0x208e3, 0x209f2, 0x20b00, 0x20d8a };
	static const u32 sco[14] = { 0x2bdac, 0x2bf21, 0x2c095, 0x2c209, 0x2c37e, 0x2c4f2, 0x2c666,
		0x2c7db, 0x2c94f, 0x2cac3, 0x2cc38, 0x2cdac, 0x2cf21, 0x2d29e };
	static const u16 cck_reg[8] = { 0x45bc, 0x45cc, 0x45d0, 0x45d4, 0x45d8, 0x45c0, 0x45c4, 0x45c8 };
	static const u32 cck[2][8] = {
		{ 0x3d23ff, 0x29b354, 0x0fc1c8, 0xfdb053, 0xf86f9a, 0xfaef92, 0xfe5fcc, 0xffdff5 },
		{ 0x3b13ff, 0x1c42de, 0xfdb0ad, 0xf60f6e, 0xfd8f92, 0x02d011, 0x01c02c, 0xfff00a },
	};
	unsigned int i, path, frequency = center == 14 ? 2484 : 2407 + center * 5;

	/* Exact ctrl_bw_ch@2f35fc: SCO CCK → channel → bandwidth → enable. */
	rtl_ax_channel_field(io, 0x4a1c, 0x7ffff, barker[center - 1]);
	rtl_ax_channel_field(io, 0x4a20, 0x7ffff, sco[center - 1]);
	rtl_ax_channel_field(io, 0x4738, BIT(17), 1);
	rtl_ax_channel_field(io, 0x4aa4, BIT(17), 1);
	rtl_ax_channel_field(io, 0x4e30, 0x1fff, frequency);
	rtl_ax_channel_field(io, 0x4974, 0x7f, (262144 + frequency / 2) / frequency);
	for (i = 0; i < 8; i++)
		rtl_ax_channel_field(io, cck_reg[i], 0xffffff, cck[center == 14][i]);
	rtl_ax_xb_gain2g(io, &phy->gain[RTL_AX_PHY_FEM]);
	rtl_ax_xb_gain_cal2g(io, phy, dm, channel);
	rtl_ax_channel_field(io, 0x0734, 0xff0000, center);
	rtl_ax_channel_field(io, 0x4974, 0xc0000000, 0);
	rtl_ax_channel_field(io, 0x4978, 0x3000, 0);
	rtl_ax_channel_field(io, 0x4978, 0x0f00, 0);
	rtl_ax_channel_field(io, 0x4c00, BIT(4), 0);
	rtl_ax_channel_field(io, 0x4cc4, BIT(4), 0);
	rtl_ax_channel_field(io, 0x4e30, BIT(26), 0);
	rtl_ax_channel_field(io, 0x4424, BIT(2), 0);
	rtl_ax_channel_field(io, 0x4ab0, BIT(0), 1);
	for (path = 0; path < 2; path++) {
		unsigned int base = path ? 0xc100 : 0xc000;

		/* bw_setting@2ede40: actual factory_trim=cc and ADC low5=f9&31=25.
		 * Branch2ee188, followed by hardwareCV1 branch2ee078. */
		rtl_ax_channel_field(io, base + 0xe4, 0x30, 2);
		rtl_ax_channel_field(io, base + 0xd4, 0x780, 8);
		rtl_ax_channel_field(io, base + 0xd4, 0x7800, 2);
		rtl_ax_channel_field(io, base + 0xd4, 0x0c000000, 2);
		rtl_ax_channel_field(io, base + 0xd8, 0x1e0, 4);
		rtl_ax_channel_field(io, base + 0xc4, 0x3e0000, 15);
		rtl_ax_channel_field(io, base + 0xe8, 0xffff0000, 10);
		rtl_ax_channel_field(io, base + 0xec, 0xff0000, 3);
		rtl_ax_channel_field(io, path ? 0x32a0 : 0x12a0, 0xff800000, 146);
		/* The actual helper uses c000[18] for both path calls. */
		rtl_ax_channel_field(io, 0xc000, BIT(18), 0);
		rtl_ax_channel_field(io, base + 0xec, 0x6000, 2);
	}
	rtl_ax_channel_field(io, 0x0c9c, 0xff000, 255);
	rtl_ax_channel_field(io, 0x0ca0, 0xff000, 0);
	rtl_ax_channel_field(io, 0x0700, BIT(5), 1);
	rtl_ax_channel_field(io, 0x2320, BIT(0), 0);
	rtl_ax_channel_field(io, 0x0c80, BIT(31), 0);
	/* RFE54 skips loop_filter's RFE11..20 exemption; BW20 selects2. */
	rtl_ax_channel_field(io, 0x440c, 0xf8000, 2);
	rtl_ax_channel_field(io, 0x4df4, 31, 2);
	rtl_ax_channel_field(io, 0x4404, 0xf8000, 2);
	rtl_ax_channel_field(io, 0x4da0, 0x3e000000, 2);
	/* Actual BAND2G condition at2f3694 reaches2f3954:spur disabled. */
	rtl_ax_channel_field(io, 0x4c14, BIT(12), 0);
	rtl_ax_channel_field(io, 0x4cd8, BIT(12), 0);
	rtl_ax_channel_field(io, 0x42c4, BIT(23), 0);
	rtl_ax_channel_field(io, 0x4c4c, BIT(12), 0);
	rtl_ax_channel_field(io, 0x4d10, BIT(12), 0);
	rtl_ax_channel_field(io, 0x4450, BIT(24), 0);
	rtl_ax_channel_field(io, 0x58dc, 0xc0000000, 1);
	rtl_ax_channel_field(io, 0x58dc, 0xc0000000, 3);
	rtl_ax_channel_field(io, 0x78dc, 0xc0000000, 1);
	rtl_ax_channel_field(io, 0x78dc, 0xc0000000, 3);
	rtl_ax_channel_field(io, 0x1200, 0x70000000, 7);
	rtl_ax_channel_field(io, 0x3200, 0x70000000, 7);
	io->delay_us(io->ctx, 1);
	rtl_ax_channel_field(io, 0x0704, BIT(1), 1);
	rtl_ax_channel_field(io, 0x0704, BIT(1), 0);
	rtl_ax_channel_field(io, 0x1200, 0x70000000, 0);
	rtl_ax_channel_field(io, 0x3200, 0x70000000, 0);
	rtl_ax_channel_field(io, 0x0704, BIT(1), 1);
}

static inline int rtl_ax_xb_rf2g20(const struct rtl_ax_io *io,
	unsigned int center, struct rtl_ax_channel *channel)
{
	static const u32 addresses[2] = { 0x18, 0x10018 };
	u32 value, be = center <= 4 ? 0xa0 : center <= 10 ? 0x800a2 : center <= 13 ? 0x800a4 : 0xa5;
	unsigned int path, bank, attempt;
	int ret;

	/* ctrl_ch@33ba58 calls A analog/digital, then B analog/digital.
	 * Each ch_setting@33b72c also applies2.4GHz RFbe before RF18. */
	for (path = 0; path < 2; path++)
		for (bank = 0; bank < 2; bank++) {
			ret = rtl_ax_phy_rf_read(io, path, addresses[bank], &value);
			if (ret)
				return ret;
			value = (value & 0xc1c00) | center;
			ret = rtl_ax_phy_rf_write_masked(io, path, 0xbe, 0xfffff, be);
			if (ret)
				return ret;
			/* Original A-only verify/rewrite loop:30 observations,5us delays. */
			if (!path)
				for (attempt = 0; attempt < 30; attempt++) {
					u32 observed;

					ret = rtl_ax_phy_rf_read(io, 0, 0xbe, &observed);
					if (ret)
						return ret;
					if (observed == be)
						break;
					io->delay_us(io->ctx, 5);
					ret = rtl_ax_phy_rf_write_masked(io, 0, 0xbe, 0xfffff, be);
					if (ret)
						return ret;
				}
			io->delay_us(io->ctx, 5);
			ret = rtl_ax_phy_rf_write_masked(io, path, addresses[bank], 0xfffff, value);
			if (ret)
				return ret;
			io->delay_us(io->ctx, 100);
		}
	/* ctrl_bw@33b628 writes both banks perpath, then op5k host backup. */
	for (path = 0; path < 2; path++)
		for (bank = 0; bank < 2; bank++) {
			ret = rtl_ax_phy_rf_read(io, path, addresses[bank], &value);
			if (ret)
				return ret;
			value = (value & ~0xc00) | 0xc00;
			ret = rtl_ax_phy_rf_write_masked(io, path, addresses[bank], 0xfffff, value);
			if (ret)
				return ret;
			channel->rf18[path][bank] = value;
		}
	return 0;
}

static inline int rtl_ax_xb_receive_channel20(const struct rtl_ax_io *io,
	const struct rtl_ax_phy_inputs *phy, const struct rtl_ax_bb_dm *dm,
	unsigned int center, struct rtl_ax_channel *channel)
{
	int ret;

	if (center < 1 || center > 14)
		return -EINVAL;
	/* cfg_mac_bw@21d980,BAND2G/PHY0/BW20:clear bits0/1/4. */
	rtl_ax_update8(io, 0xc010, 3, 0);
	rtl_ax_update8(io, 0xc628, 0x13, 0x10);
	io->write32(io->ctx, 0xc088, 0);
	rtl_ax_xb_bb2g20(io, phy, dm, center, channel);
	ret = rtl_ax_xb_rf2g20(io, center, channel);
	if (ret)
		return ret;
	channel->band = 0;
	channel->primary = channel->center = center;
	channel->bandwidth = 0;
	channel->configured = true;
	return 0;
}
#endif
