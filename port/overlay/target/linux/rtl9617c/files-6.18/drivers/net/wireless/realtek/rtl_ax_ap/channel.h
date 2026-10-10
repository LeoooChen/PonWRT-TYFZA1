/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_CHANNEL_H
#define RTL_AX_AP_CHANNEL_H
#include "phy.h"
#include "bb-dm.h"

struct rtl_ax_channel {
	bool configured;
	u8 band, primary, center, bandwidth;
	s8 external_loss[2];
	u32 rf18[2][2];
};

static inline void rtl_ax_channel_field(const struct rtl_ax_io *io,
	u32 address, u32 mask, u32 value)
{
	unsigned int shift = 0;

	while (!(mask & BIT(shift)))
		shift++;
	rtl_ax_update32(io, address | 0x10000, mask, (value << shift) & mask);
}

static inline void rtl_ax_c_channel_gain(const struct rtl_ax_io *io,
	const struct rtl_ax_phy_gain *gain, unsigned int band, unsigned int path)
{
	/* Actual set_gain_error_C@2e8ea4. Arrays checked against this module's
	 * .rodata+26140/260c8/26178/261b0/261d0/26208/26240. */
	static const u16 lna[2][7] = {
		{ 0x45dc, 0x45dc, 0x4660, 0x4660, 0x4660, 0x4660, 0x4664 },
		{ 0x4740, 0x4740, 0x4744, 0x4744, 0x4744, 0x4744, 0x4748 },
	};
	static const u32 lna_mask[7] = { 0xff0000, 0xff000000, 0xff, 0xff00, 0xff0000, 0xff000000, 0xff };
	static const u16 bypass[2][7] = {
		{ 0x4bb4, 0x4bb4, 0x4bb4, 0x4bb4, 0x4bb8, 0x4bb8, 0x4bb8 },
		{ 0x4c78, 0x4c78, 0x4c78, 0x4c78, 0x4c7c, 0x4c7c, 0x4c7c },
	};
	static const u32 packed_mask[7] = { 0xff, 0xff00, 0xff0000, 0xff000000, 0xff, 0xff00, 0xff0000 };
	static const u16 op1db[2][7] = {
		{ 0x4668, 0x4668, 0x4668, 0x4668, 0x466c, 0x466c, 0x466c },
		{ 0x474c, 0x474c, 0x474c, 0x474c, 0x4750, 0x4750, 0x4750 },
	};
	static const u16 tia_lna[2][7] = {
		{ 0x4670, 0x4670, 0x4670, 0x4670, 0x4674, 0x4674, 0x4674 },
		{ 0x4754, 0x4754, 0x4754, 0x4754, 0x4758, 0x4758, 0x4758 },
	};
	static const u16 wideband_lna[2][4] = {
		{ 0x46a0, 0x46bc, 0x4720, 0x4724 },
		{ 0x49f4, 0x4a70, 0x4a74, 0x4a78 },
	};
	static const u16 wideband_elna[2] = { 0x4734, 0x4aa0 };
	static const u16 gs[2][2] = { { 0x46a8, 0x46ac }, { 0x4a5c, 0x4a60 } };
	unsigned int i;
	u32 value;

	for (i = 0; i < 7; i++) {
		rtl_ax_channel_field(io, lna[path][i], lna_mask[i], (u8)gain->lna[band][path][i]);
		rtl_ax_channel_field(io, bypass[path][i], packed_mask[i], (u8)gain->lna_bypass[band][path][i]);
		rtl_ax_channel_field(io, op1db[path][i], packed_mask[i], (u8)gain->lna_op1db[band][path][i]);
		rtl_ax_channel_field(io, tia_lna[path][i], packed_mask[i], (u8)gain->tia_lna_op1db[band][path][i]);
	}
	rtl_ax_channel_field(io, path ? 0x4758 : 0x4674, 0xff000000,
		(u8)gain->tia_lna_op1db[band][path][7]);
	rtl_ax_channel_field(io, path ? 0x4748 : 0x4664, 0xff0000, (u8)gain->tia[band][path][0]);
	rtl_ax_channel_field(io, path ? 0x4748 : 0x4664, 0xff000000, (u8)gain->tia[band][path][1]);
	/* RFE54 follows the original external FEM branch (>50). */
	rtl_ax_channel_field(io, wideband_elna[path], 0x3fffc0, gain->wb_elna[band][path]);
	rtl_ax_channel_field(io, wideband_lna[path][0], 0xe0000000, gain->wb_lna_tia[band][path][0]);
	value = gain->wb_lna_tia[band][path][1] | (gain->wb_lna_tia[band][path][2] << 3);
	rtl_ax_channel_field(io, wideband_lna[path][1], 0x3f000000, value);
	value = 0;
	for (i = 0; i < 10; i++)
		value |= gain->wb_lna_tia[band][path][i + 3] << (3 * i);
	rtl_ax_channel_field(io, wideband_lna[path][2], 0x3fffffff, value);
	value = gain->wb_lna_tia[band][path][13] | (gain->wb_lna_tia[band][path][14] << 3) |
		(gain->wb_lna_tia[band][path][15] << 6);
	rtl_ax_channel_field(io, wideband_lna[path][3], 0x1ff, value);
	rtl_ax_channel_field(io, gs[path][0], 0x7fff8000, gain->gs[band][path][0] & 0xffff);
	value = (gain->gs[band][path][0] >> 16) | (gain->gs[band][path][1] << 16);
	rtl_ax_channel_field(io, gs[path][1], 0x0fffffff, value);
	value = gain->elna[band][path][0] | (gain->elna[band][path][1] << 8);
	rtl_ax_channel_field(io, path ? 0x4740 : 0x45dc, 0xffff, value);
}

static inline void rtl_ax_c_channel_calibration(const struct rtl_ax_io *io,
	const struct rtl_ax_phy_inputs *phy, const struct rtl_ax_bb_dm *dm,
	unsigned int band, unsigned int path, struct rtl_ax_channel *channel)
{
	/* Original hidden calibration uses CS for RFE54; the real C OTP
	 * was all ff, so its actual board path skips this write. */
	if (dm->hidden_gain_valid)
		rtl_ax_channel_field(io, path ? 0x4c88 : 0x4bc4, 0xff00,
			(u8)(dm->hidden_cs[path][band] * 4));
	if (dm->normal_gain_valid) {
		int normal = -dm->normal_gain[path][band + 1];
		int offset = phy->gain_offset[0] >> 2;

		/* set_normal_efuse_C@2e8a04: non-DBCC both paths use PHY0.
		 * Capture external loss for subsequent dynamic EDCCA host policy. */
		channel->external_loss[path] = normal * 4 + offset;
		rtl_ax_channel_field(io, path ? 0x4778 : 0x4694, 0xff0000,
			(u8)(-channel->external_loss[path]));
		rtl_ax_channel_field(io, 0x4e0c, path ? 0xff0000 : 0xff00, (u8)(normal * 16));
		rtl_ax_channel_field(io, 0x4e2c, path ? 0xff00 : 0xff, (u8)(normal * 16));
	}
}

static inline unsigned int rtl_ax_channel_primary_index(unsigned int primary,
	unsigned int center, unsigned int bandwidth)
{
	if (!bandwidth)
		return 0;
	return (primary > center ? 1 : 2) +
		(bandwidth == 2 && abs((int)primary - (int)center) == 6 ? 2 : 0);
}

static inline void rtl_ax_c_adc(const struct rtl_ax_io *io, unsigned int path,
	unsigned int bandwidth)
{
	unsigned int base = path ? 0xc100 : 0xc000;

	/* Actual adc_cfg_C@2e333c BW20/40/80, RFE54. These are BB addresses,
	 * including c0xx/c1xx; use the 0x10000 BB window. */
	rtl_ax_channel_field(io, base + 0xd4, 0x780, 8);
	rtl_ax_channel_field(io, base + 0xe8, 0xffff0000, 9);
	rtl_ax_channel_field(io, base + 0xe4, 0x30, 2);
	rtl_ax_channel_field(io, path ? 0x32a0 : 0x12a0, 0xff800000, 73);
	rtl_ax_channel_field(io, base + 0xec, 0x6000, 0);
	rtl_ax_channel_field(io, base + 0xd4, 0x7800, 2);
	rtl_ax_channel_field(io, base + 0xd4, 0x0c000000, bandwidth == 2 ? 2 : 3);
	rtl_ax_channel_field(io, base + 0xd8, 0x1e0, bandwidth == 2 ? 8 : 15);
	rtl_ax_channel_field(io, base + 0xc4, 0x3e0000, 0);
	rtl_ax_channel_field(io, base + 0xec, 0xff0000, bandwidth == 2 ? 3 : 2);
}

static inline void rtl_ax_c_5m_mask(const struct rtl_ax_io *io,
	unsigned int primary, unsigned int bandwidth)
{
	static const u16 addresses[] = { 0x4c4c, 0x4d10 };
	bool enable = bandwidth == 1 || (bandwidth == 2 && (primary == 3 || primary == 4));
	bool lower = bandwidth == 1 ? primary == 2 : primary == 4;
	unsigned int i;

	/* Exact halbb_5m_mask_C@2e37a4, including the actual channel
	 * argument forwarded by ctrl_bw_ch_C@2e9b34. */
	for (i = 0; i < ARRAY_SIZE(addresses); i++) {
		if (enable) {
			rtl_ax_channel_field(io, addresses[i], 0x3f, 4);
			rtl_ax_channel_field(io, addresses[i], BIT(12), 1);
			rtl_ax_channel_field(io, addresses[i], BIT(8), !lower);
			rtl_ax_channel_field(io, addresses[i], BIT(6), lower);
		} else
			rtl_ax_channel_field(io, addresses[i], BIT(12), 0);
	}
	rtl_ax_channel_field(io, 0x4450, BIT(24), enable);
}

static inline void rtl_ax_c_bb_5g(const struct rtl_ax_io *io,
	const struct rtl_ax_phy_inputs *phy, const struct rtl_ax_bb_dm *dm,
	unsigned int primary, unsigned int center, unsigned int bandwidth, struct rtl_ax_channel *channel)
{
	static const u16 cck_addresses[8] = { 0x45bc, 0x45cc, 0x45d0, 0x45d4, 0x45d8, 0x45c0, 0x45c4, 0x45c8 };
	static const u32 cck_values[8] = { 0x3d23ff, 0x29b354, 0x0fc1c8, 0xfdb053, 0xf86f9a, 0xfaef92, 0xfe5fcc, 0xffdff5 };
	unsigned int band = center <= 64 ? 1 : center <= 144 ? 2 : 3;
	unsigned int path, i, frequency = 5000 + center * 5;

	/* ctrl_ch_C@2e9430: A gain/cal/mode, then B; non-DBCC PHY0. */
	for (path = 0; path < 2; path++) {
		rtl_ax_c_channel_gain(io, &phy->gain[RTL_AX_PHY_FEM], band, path);
		rtl_ax_c_channel_calibration(io, phy, dm, band, path, channel);
		rtl_ax_channel_field(io, path ? 0x4aa4 : 0x4738, BIT(17), 0);
	}
	rtl_ax_channel_field(io, 0x4970, BIT(1), 0);
	rtl_ax_channel_field(io, 0x4e30, 0x1fff, frequency);
	rtl_ax_channel_field(io, 0x4974, 0x7f, (262144 + frequency / 2) / frequency);
	for (i = 0; i < 8; i++)
		rtl_ax_channel_field(io, cck_addresses[i], 0xffffff, cck_values[i]);
	/* Actual ctrl_ch_C@2e9648 passes center directly to0734, unlike
	 * the newer SDK's ch_idx_encode call. */
	rtl_ax_channel_field(io, 0x0734, 0xff0000, center);
	/* ctrl_bw_C@2e4238,20/40/80MHz and both ADC paths. */
	rtl_ax_channel_field(io, 0x4974, 0xc0000000, bandwidth);
	rtl_ax_channel_field(io, 0x4978, 0x3000, 0);
	rtl_ax_channel_field(io, 0x4978, 0x0f00,
		rtl_ax_channel_primary_index(primary, center, bandwidth));
	rtl_ax_channel_field(io, 0x4e30, BIT(26), bandwidth == 1);
	rtl_ax_channel_field(io, 0x4424, BIT(2), bandwidth == 1);
	rtl_ax_c_adc(io, 0, bandwidth);
	rtl_ax_c_adc(io, 1, bandwidth);
	rtl_ax_channel_field(io, 0x0c9c, 0xff000, bandwidth ? 0 : 255);
	rtl_ax_channel_field(io, 0x0ca0, 0xff000, 0);
	/* ctrl_bw_ch_C@2e9a40: 5GHz CCK off. RFE54 skips iFEM spur
	 * suppression and ctrl_btg_C returns immediately for RFE>49. */
	rtl_ax_channel_field(io, 0x0700, BIT(5), 0);
	rtl_ax_channel_field(io, 0x2320, BIT(0), 1);
	rtl_ax_channel_field(io, 0x0c80, BIT(31), 1);
	rtl_ax_channel_field(io, 0x49e8, BIT(7), 0);
	rtl_ax_c_5m_mask(io, primary, bandwidth);
	for (path = 0; path < 2; path++) {
		rtl_ax_channel_field(io, path ? 0x78dc : 0x58dc, 0xc0000000, 1);
		rtl_ax_channel_field(io, path ? 0x78dc : 0x58dc, 0xc0000000, 3);
	}
	rtl_ax_channel_field(io, 0x5818, BIT(30), 1);
	rtl_ax_channel_field(io, 0x7818, BIT(30), 1);
	rtl_ax_channel_field(io, 0x5818, BIT(30), 0);
	rtl_ax_channel_field(io, 0x7818, BIT(30), 0);
	/* Exact bb_reset_all_C@2e25e8. */
	rtl_ax_channel_field(io, 0x1200, 0x70000000, 7);
	rtl_ax_channel_field(io, 0x3200, 0x70000000, 7);
	io->delay_us(io->ctx, 1);
	rtl_ax_channel_field(io, 0x0704, BIT(1), 1);
	rtl_ax_channel_field(io, 0x0704, BIT(1), 0);
	rtl_ax_channel_field(io, 0x1200, 0x70000000, 0);
	rtl_ax_channel_field(io, 0x3200, 0x70000000, 0);
	rtl_ax_channel_field(io, 0x0704, BIT(1), 1);
}

static inline int rtl_ax_c_rf_5g(const struct rtl_ax_io *io, unsigned int center, unsigned int bandwidth,
	struct rtl_ax_channel *channel)
{
	static const u32 addresses[2] = { 0x18, 0x10018 };
	unsigned int path, bank;
	int ret;

	/* Exact ctl_band_ch_bw_C@30b1a0, PHY0/non-DBCC/board hardwareCV1.
	 * First read all four values, then A analog/digital, B analog/digital.
	 * Unlike the newer SDK, this board function has no trailing IQG toggle. */
	for (path = 0; path < 2; path++)
		for (bank = 0; bank < 2; bank++) {
			ret = rtl_ax_phy_rf_read(io, path, addresses[bank], &channel->rf18[path][bank]);
			if (ret)
				return ret;
		}
	for (path = 0; path < 2; path++)
		for (bank = 0; bank < 2; bank++) {
			u32 value = channel->rf18[path][bank];

			value = (value & ~0x3efff) | center | BIT(16) | BIT(8) |
				(bandwidth == 2 ? BIT(10) : BIT(11) | (bandwidth ? 0 : BIT(10)));
			value = (value & 0xf0fff) | BIT(12);
			ret = rtl_ax_phy_rf_write_masked(io, path, addresses[bank], 0xfffff, value);
			if (ret)
				return ret;
			channel->rf18[path][bank] = value;
			io->delay_us(io->ctx, 100);
		}
	/* rxbb_bw_C@30da54: perpath ee[2]=1,33[4:0]=10,
	 * LUT3f[5:0]=27/19/11 for20/40/80MHz,ee[2]=0. */
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 0xee, 4, 1);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x33, 31, 10);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x3f, 63, 27 - bandwidth * 8);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0xee, 4, 0);
		if (ret)
			return ret;
	}
	return 0;
}

static inline int rtl_ax_c_receive_channel(const struct rtl_ax_io *io,
	const struct rtl_ax_phy_inputs *phy, const struct rtl_ax_bb_dm *dm,
	unsigned int primary, unsigned int center, unsigned int bandwidth, struct rtl_ax_channel *channel)
{
	int ret;

	/* Channel ranges from the actual ctrl_ch_C validation. */
	if (!((center >= 36 && center <= 64) || (center >= 100 && center <= 144) ||
	      (center >= 149 && center <= 177)))
		return -EINVAL;
	/* cfg_mac_bw@21d980,PHY0/MACband0,normal20/40/80MHz. */
	rtl_ax_update8(io, 0xc010, 3, bandwidth);
	rtl_ax_update8(io, 0xc628, 0x13, 0x13);
	io->write32(io->ctx, 0xc088,
		rtl_ax_channel_primary_index(primary, center, bandwidth) |
		(bandwidth == 2 ? (primary > center ? 9 : 10) << 4 : 0));
	rtl_ax_c_bb_5g(io, phy, dm, primary, center, bandwidth, channel);
	ret = rtl_ax_c_rf_5g(io, center, bandwidth, channel);
	if (ret)
		return ret;
	channel->band = 1;
	channel->primary = primary;
	channel->center = center;
	channel->bandwidth = bandwidth;
	channel->configured = true;
	return 0;
}
#endif
