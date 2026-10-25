/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_BB_DM_H
#define RTL_AX_AP_BB_DM_H
#include "power.h"
#include "board-cal.h"

struct rtl_ax_bb_dm {
	bool ic_ready, gpio_ready, gain_ready, normal_gain_valid, hidden_gain_valid;
	s8 normal_gain[2][5], hidden_cs[2][12], hidden_cg[2][12];
	u8 hidden[55], hidden_bands;
	u64 supportability;
	u32 physts_bitmap[16];
	bool physts_ready;
	bool reports_ready, txdiff_ready, macid_power_ready;
	bool statistics_ready, counter_reset;
	bool la_ready, psd_ready, edcca_ready;
	u32 la_buffer_bytes, la_samples, la_trigger_time;
	u8 la_buffer_mode, la_repeat;
	u16 psd_fft_points, psd_software_average;
	u8 psd_hardware_average, psd_iq, psd_path, psd_input, psd_channel, psd_center;
	u8 edcca_mode, edcca_high, edcca_low, edcca_low_bound, edcca_power_offset;
	u8 edcca_cap_2g, edcca_cap_5g, edcca_cap_6g, edcca_cap_carrier;
	u16 crc_rate[4];
	u8 crc_frame_type;
	u8 histogram_threshold[3][11];
	u64 report_packets;
	u64 debug_components;
	u8 debug_port_profile, debug_port_previous_profile;
	u32 monitor_register_count;
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

		/* PAPE byte0=4,TRSW byte1=6. RFE54 uses LNA byte2=a
		 * on XB; C reserves that setting for RFE63/64. */
		rtl_ax_update32(io, reg, 255, 4);
		rtl_ax_update32(io, reg, 255U << 8, 6U << 8);
		if (chip == RTL_AX_8192XB)
			rtl_ax_update32(io, reg, 255U << 16, 10U << 16);
	}
	if (chip == RTL_AX_8852C)
		for (path = 0; path < 2; path++)
			rtl_ax_update32(io, path ? 0x17894 : 0x15894, 255U << 16, 8U << 16);
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

static inline u32 rtl_ax_bb_physts_get(const struct rtl_ax_io *io, unsigned int page)
{
	/* Original page9 is reserved;page10..15 skip it in the CR array. */
	if (page == 9)
		return 0;
	return io->read32(io->ctx, 0x1073c + (page - (page > 9)) * 4);
}

static inline void rtl_ax_bb_physts_set(const struct rtl_ax_io *io,
	unsigned int page, u32 bitmap, struct rtl_ax_bb_dm *dm)
{
	if (page == 9)
		return;
	dm->physts_bitmap[page] = bitmap;
	io->write32(io->ctx, 0x1073c + (page - (page > 9)) * 4, bitmap);
}

static inline void rtl_ax_bb_dm_physts_start(const struct rtl_ax_io *io, unsigned int chip,
	bool independent_xtal, bool antenna_diversity, struct rtl_ax_bb_dm *dm)
{
	unsigned int page;
	u32 bitmap;

	/* Actual normal-mode supportability_default@2b03dc. User-controlled
	 * platform inputs retain the original independent-crystal/diversity policy. */
	dm->supportability = chip == RTL_AX_8852C ? 0x38fa : 0x8fb;
	if (!independent_xtal)
		dm->supportability &= ~BIT_ULL(6);
	if (antenna_diversity)
		dm->supportability |= BIT_ULL(10);
	/* Both endpoints use original BB_CLIENT CR type2,while MAC/AP policy
	 * remains AP. cr_cfg_physts_init@2c6aa4 sets bitmap array base073c. */
	for (page = 0; page < 16; page++) {
		bitmap = rtl_ax_bb_physts_get(io, page);
		if (page > 10) {
			rtl_ax_bb_physts_set(io, page, bitmap | BIT(9), dm);
			bitmap = rtl_ax_bb_physts_get(io, page);
		}
		dm->physts_bitmap[page] = bitmap;
	}
	/* Actual physts_parsing_init enables IE13 for CCK/OFDM. */
	for (page = 6; page <= 7; page++)
		rtl_ax_bb_physts_set(io, page, rtl_ax_bb_physts_get(io, page) | BIT(13), dm);
	if (dm->supportability & BIT_ULL(3))
		for (page = 0; page < 16; page++) {
			if ((page >= 4 && page <= 7) || (page >= 9 && page <= 11))
				continue;
			rtl_ax_bb_physts_set(io, page, rtl_ax_bb_physts_get(io, page) | BIT(24), dm);
		}
	dm->physts_ready = true;
}

static const u32 rtl_ax_txdiff_table[48] = {
	0,0,0,0,
	0x11111,0x11111,0x11111,0x11111,
	0x22211,0x22222,0x22222,0x22222,
	0x22211,0x33333,0x33333,0x33333,
	0x22211,0x44433,0x44444,0x44444,
	0x21111,0x43332,0x55554,0x55555,
	0x22222,0x44433,0x66655,0x66666,
	0x33321,0x54443,0x76655,0x77777,
	0x32111,0x44444,0x66655,0x88877,
	0x22221,0x55443,0x76655,0x88877,
	0x22111,0x44333,0x66665,0x88777,
	0x22211,0x44433,0x77654,0x88777,
};

static inline void rtl_ax_bb_dm_reports_start(const struct rtl_ax_io *io,
					    struct rtl_ax_bb_dm *dm)
{
	static const u8 histograms[3][11] = {
		{5,8,11,14,17,20,23,26,29,32,35},
		{2,3,4,5,6,8,10,12,14,16,18},
		{1,5,10,15,20,30,60,90,120,150,200},
	};
	unsigned int i;

	/* Actual common report@296fe4 initializes host histogram and counters;
	 * debug init@29b47c enables two BB debug-port clocks. */
	memcpy(dm->histogram_threshold, histograms, sizeof(histograms));
	dm->report_packets = 0;
	dm->monitor_register_count = 50;
	dm->debug_components = 0;
	dm->debug_port_profile = dm->debug_port_previous_profile = 2;
	rtl_ax_update32(io, 0x120f4, 0, BIT(24));
	rtl_ax_update32(io, 0x120f8, 0, BIT(31));
	dm->reports_ready = true;
	/* halbb_txdiff_tbl_init@2c7ebc copies192B of actual board data.
	 * mac_write_pwr_reg@21ecb8 uses direct MAC offsets,without BB prefix. */
	for (i = 0; i < ARRAY_SIZE(rtl_ax_txdiff_table); i++)
		io->write32(io->ctx, 0xd56c + i * 4, rtl_ax_txdiff_table[i]);
	dm->txdiff_ready = true;
	/* Actual macid_ctrl_init@2c7e0c:128 DWORDs covering256 MACIDs,
	 * band0 only in this original board's non-DBCC startup. */
	for (i = 0; i < 128; i++)
		io->write32(io->ctx, 0xd36c + i * 4, 0);
	dm->macid_power_ready = true;
}

static inline void rtl_ax_bb_dm_statistics_start(const struct rtl_ax_io *io,
					       struct rtl_ax_bb_dm *dm)
{
	/* Actual statistics_init@2cfad0 has no hardwarecounter reset;it resets
	 * host counters and selects CRC rates/formats. BB_CLIENT's selectors
	 * from cr_cfg_stat_init@2cfb48 all reside in BB0900. */
	dm->counter_reset = true;
	dm->crc_rate[0] = 4;
	dm->crc_rate[1] = 128;
	dm->crc_rate[2] = 256;
	dm->crc_rate[3] = 384;
	dm->crc_frame_type = 8; /* Actual STATE_BEACON2→MAC subtype8. */
	/* OFDM6M maps toLSIG codeb via legacy_rate_2_spec_rate@2c9eb8. */
	rtl_ax_update32(io, 0x10900, 0xf, 0xb);
	rtl_ax_update32(io, 0x10900, 0x7f0, 0); /* HT MCS0. */
	rtl_ax_update32(io, 0x10900, 0x7800, 0); /* VHT MCS0. */
	rtl_ax_update32(io, 0x10900, 0x180000, 0); /* VHT NSS1. */
	rtl_ax_update32(io, 0x10900, 0x78000, 0); /* HE MCS0. */
	rtl_ax_update32(io, 0x10900, 0x600000, 0); /* HE NSS1. */
	rtl_ax_update32(io, 0x10900, 0x1f800000, 8U << 23); /* Beacon. */
	dm->statistics_ready = true;
}

static inline void rtl_ax_bb_dm_signal_start(const struct rtl_ax_io *io, unsigned int chip,
	unsigned int edcca_mode, struct rtl_ax_bb_dm *dm)
{
	/* Original LA reset@2b2fc4 initializes trigger time404us. Its MAC
	 * common cfg@222a6c enables LA function/clock and programs exponent2,
	 * count101,enable1,while preserving9660 bits8..19. It does not capture
	 * samples or allocate a hostbuffer during startup. */
	dm->la_trigger_time = 404;
	dm->la_repeat = 1;
	rtl_ax_update32(io, 0x8400, 0, BIT(17));
	rtl_ax_update32(io, 0x8404, 0, BIT(17));
	io->write32(io->ctx, 0x9660, (io->read32(io->ctx, 0x9660) & 0x000fff00) | 0x65200001);
	/* la_mac_cfg_buf_default@2b1d34 is host configuration only. */
	dm->la_buffer_bytes = 262144;
	dm->la_buffer_mode = 3;
	dm->la_samples = dm->la_buffer_bytes / 8;
	dm->la_ready = true;
	/* Exact psd_init@2c1b94 calls para_setting(1,3,2,512,0,3,36,42,2,1).
	 * It sets parameters without triggering PSD or switching channels. */
	dm->psd_software_average = 1;
	dm->psd_hardware_average = 3;
	dm->psd_iq = 2;
	dm->psd_fft_points = 512;
	dm->psd_path = 0;
	dm->psd_input = 3;
	dm->psd_channel = 36;
	dm->psd_center = 42;
	rtl_ax_update32(io, 0x1422c, 0x18000, 1U << 15);
	rtl_ax_update32(io, 0x1422c, 0x1800, 2U << 11);
	rtl_ax_update32(io, 0x1422c, 0x6000, 3U << 13);
	rtl_ax_update32(io, 0x1422c, 0x60000, 0);
	rtl_ax_update32(io, 0x1422c, 0x180000, 3U << 19);
	dm->psd_ready = true;
	/* Actual edcca_dev_hw_cap@2a5264 C/XB raw capability defaults. AP
	 * regulatory/UCI policy can override them when its lifecycle is added. */
	dm->edcca_cap_2g = chip == RTL_AX_8852C ? 65 : 68;
	dm->edcca_cap_5g = chip == RTL_AX_8852C ? 60 : 63;
	dm->edcca_cap_6g = chip == RTL_AX_8852C ? 53 : 0;
	dm->edcca_cap_carrier = chip == RTL_AX_8852C ? 65 : 75;
	/* edcca_init@2a52d4 initializes host limits and readsBB power offset;
	 * its only write sets0c70 collisionT2R state41,for9.6us timing. */
	dm->edcca_mode = edcca_mode;
	dm->edcca_high = dm->edcca_low = 249;
	dm->edcca_low_bound = 46;
	dm->edcca_power_offset = (io->read32(io->ctx, 0x14848) >> 17) & 31;
	rtl_ax_update32(io, 0x10c70, 0x3f00000, 41U << 20);
	dm->edcca_ready = true;
}

/* Normal mode: edcca_thre_calc/transfer_rssi@2a3a60/2a3a20 and
 * set_collision_th@2a38c8, using the CLIENT CR map on both chips. */
static inline void rtl_ax_bb_dm_edcca_update(const struct rtl_ax_io *io,
	struct rtl_ax_bb_dm *dm, bool linked, u8 rssi_min)
{
	unsigned int high = linked ? (rssi_min >> 1) + 15 : 249;
	int collision = 60 - (rssi_min >> 1);
	unsigned int i, shift, address;

	/* Adaptivity/carrier-sense modes have different regulatory inputs. */
	if (dm->edcca_mode) return;
	if (linked && high < 66) high = 66;
	dm->edcca_high = high;
	dm->edcca_low = high - 8;
	rtl_ax_update32(io, 0x14840, 0xff00, high << 8);
	rtl_ax_update32(io, 0x14840, 0xff, high);
	rtl_ax_update32(io, 0x14840, 0xff000000, high << 24);
	rtl_ax_update32(io, 0x14848, 0x1f000, 8U << 12);
	if (collision < 0) collision = 0;
	if (collision > 31) collision = 31;
	rtl_ax_update32(io, 0x10c70, 0xf800, collision << 11);
	for (i = 0; i < 13; i++) {
		address = 0x10c74 + (i / 6) * 4;
		shift = (i % 6) * 5;
		rtl_ax_update32(io, address, 31U << shift, (u32)collision << shift);
	}
}
#endif
