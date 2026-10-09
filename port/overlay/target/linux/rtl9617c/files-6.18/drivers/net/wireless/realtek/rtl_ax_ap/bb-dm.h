/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_BB_DM_H
#define RTL_AX_AP_BB_DM_H
#include "power.h"
#include "board-cal.h"

struct rtl_ax_bb_dm {
	bool ic_ready, gpio_ready, gain_ready, normal_gain_valid, hidden_gain_valid;
	s8 normal_gain[2][5], hidden_cs[2][12], hidden_cg[2][12];
	u8 hidden[55], hidden_bands;
	bool tdma_cr_enabled, tdma_cr_state;
	u32 tdma_cr_period[2], tdma_cr_index;
	bool dynamic_csi_response, csi_response, dynamic_csi_debug;
	u32 channel_estimate_delay, phy_status_delay, max_estimate_tones, dcr_bandwidth;
	u8 cck_packet_threshold, cck_rssi_offset, cck_sbd_threshold;
	u8 factory_trim, adc_timing_trim;
};

static inline int rtl_ax_bb_dm_ic_start(const struct rtl_ax_io *io, unsigned int chip,
				      struct rtl_ax_bb_dm *dm)
{
	int ret;

	/* halbb_tdma_cr_sel_init@297314 only initializes these host fields. */
	dm->tdma_cr_enabled = false;
	dm->tdma_cr_period[0] = dm->tdma_cr_period[1] = 50;
	dm->tdma_cr_index = 0;
	dm->tdma_cr_state = false;
	if (chip == RTL_AX_8852C) {
		/* Actual C IC init@2e14c4 enables DBCC80+80 EVM report selection. */
		rtl_ax_update32(io, 0x10a10, 0, 1);
		rtl_ax_update32(io, 0x12a10, 0, 1);
	} else {
		/* Actual XB@2ebf68: T2R=15*0.4us+9*0.4us=9.6us. */
		rtl_ax_update32(io, 0x10624, 0x3f, 0xf);
		rtl_ax_update32(io, 0x10608, 0x3f00, 0x900);
		dm->cck_packet_threshold = io->read32(io->ctx, 0x14b48) & 255;
		dm->cck_rssi_offset = (io->read32(io->ctx, 0x14b48) >> 16) & 255;
		dm->cck_sbd_threshold = io->read32(io->ctx, 0x14ba4) & 255;
		ret = rtl_ax_efuse_read(io, 0x5e3, &dm->factory_trim);
		if (ret)
			return ret;
		ret = rtl_ax_efuse_read(io, 0x5af, &dm->adc_timing_trim);
		if (ret)
			return ret;
	}
	/* halbb_dcr_init@2d912c also initializes host CSI response state. */
	dm->dynamic_csi_response = false;
	dm->csi_response = true;
	dm->dynamic_csi_debug = false;
	dm->channel_estimate_delay = 50;
	dm->phy_status_delay = 5;
	dm->max_estimate_tones = 54;
	dm->dcr_bandwidth = 7;
	dm->ic_ready = true;
	return 0;
}

static inline void rtl_ax_bb_dm_gpio_start(const struct rtl_ax_io *io, unsigned int chip,
					 struct rtl_ax_bb_dm *dm)
{
	unsigned int path, index;

	/* Actual gpio_setting_init_C@2e585c/XB@2f0218,board RFE54. */
	for (path = 0; path < 2; path++) {
		unsigned int base = path ? 0x17800 : 0x15800;

		rtl_ax_update32(io, base + 0x68, 0, RTL_AX_BIT(1));
		rtl_ax_update32(io, base + 0x68, RTL_AX_BIT(2), 0);
		rtl_ax_update32(io, base + 0x68, 0xe0, 0);
		io->write32(io->ctx, base + 0x80, 0x77777777);
		io->write32(io->ctx, base + 0x84, 0x77777777);
		rtl_ax_update32(io, base + 0x94, 0, RTL_AX_BIT(24));
		if (chip == RTL_AX_8192XB)
			rtl_ax_update32(io, base + 0x94, 0, RTL_AX_BIT(25));
	}
	io->write32(io->ctx, 0x10334, 0xffffffff);
	io->write32(io->ctx, 0x10338, 0);
	io->write32(io->ctx, 0x1033c, 0);
	io->write32(io->ctx, 0x10340, 0);
	for (path = 0; path < 2; path++) {
		unsigned int reg = path ? 0x17868 : 0x15868;

		/* gpio_trsw_table: index=(hardware<<2)|(tx<<1)|rx,
		 * two-bit output=(trsw_a<<1)|trsw_b,at16+2*index. */
		for (index = 0; index < 8; index++) {
			unsigned int shift = 16 + 2 * index;
			unsigned int value = (index & 3) ? 2 : 1;

			rtl_ax_update32(io, reg, 3U << shift, value << shift);
		}
	}
	for (path = 0; path < 2; path++) {
		unsigned int reg = path ? 0x17894 : 0x15894;

		/* PAPE byte0=4,TRSW byte1=6. RFE53/54 LNA inverted,
		 * standby mode=1,so LNA byte2=a instead of the default8. */
		rtl_ax_update32(io, reg, 255, 4);
		rtl_ax_update32(io, reg, 255U << 8, 6U << 8);
		if (chip == RTL_AX_8192XB)
			rtl_ax_update32(io, reg, 255U << 16, 10U << 16);
	}
	if (chip == RTL_AX_8852C)
		for (path = 0; path < 2; path++)
			rtl_ax_update32(io, path ? 0x17894 : 0x15894, 255U << 16, 10U << 16);
	dm->gpio_ready = true;
}

static inline s8 rtl_ax_bb_gain_nibble(u8 value)
{
	value &= 15;
	return value & 8 ? value - 16 : value;
}

static inline int rtl_ax_bb_dm_gain_start(const struct rtl_ax_io *io, unsigned int chip,
	const struct rtl_ax_board_cal *flash, struct rtl_ax_bb_dm *dm)
{
	const unsigned int address[2][12] = {
		{0x5bb,0x5ba,0x5b9,0x5b8,0x5b7,0x5b6,0x5b5,0x5b4,0x5b3,0x5b2,0x5b1,0x5b0},
		{0x590,0x58f,0x58e,0x58d,0x58c,0x58b,0x58a,0x589,0x588,0x587,0x586,0x585},
	};
	unsigned int i, path;
	int ret;

	/* halbb_get_normal_efuse_init calls FLASH_GET_INFO329..333 for the
	 * actual2G/5G groups: high nibble=pathA,low nibble=pathB,signed4-bit. */
	for (i = 0; i < 5; i++) {
		dm->normal_gain[0][i] = rtl_ax_bb_gain_nibble(flash->gain_2g5g[i] >> 4);
		dm->normal_gain[1][i] = rtl_ax_bb_gain_nibble(flash->gain_2g5g[i]);
		if (flash->gain_2g5g[i] != 255)
			dm->normal_gain_valid = true;
	}
	/* Actual C/XB hidden init reads0x585,55bytes. C then decodes12
	 * groups per path,XB4;the address arrays come from the board ELF. */
	for (i = 0; i < sizeof(dm->hidden); i++) {
		ret = rtl_ax_efuse_read(io, 0x585 + i, &dm->hidden[i]);
		if (ret)
			return ret;
	}
	dm->hidden_bands = chip == RTL_AX_8852C ? 12 : 4;
	for (path = 0; path < 2; path++)
		for (i = 0; i < dm->hidden_bands; i++) {
			u8 value = dm->hidden[address[path][i] - 0x585];

			dm->hidden_cs[path][i] = rtl_ax_bb_gain_nibble(value >> 4);
			dm->hidden_cg[path][i] = rtl_ax_bb_gain_nibble(value);
			if (value != 255)
				dm->hidden_gain_valid = true;
		}
	dm->gain_ready = true;
	return 0;
}
#endif
