/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_TRIM_H
#define RTL_AX_AP_TRIM_H
#include "phy.h"

struct rtl_ax_trim {
	bool ready, thermal_written, bias_written;
	u16 otp_start, otp_bytes;
	u8 otp[80], thermal[2], bias[2], thermal_code[2];
	s8 tssi[2][8], tssi_6g[2][8];
	bool tssi_erased, tssi_6g_erased;
	u32 rf43[2], rf60[2];
};

static inline void rtl_ax_trim_tssi_erased(s8 value[2][8], bool *erased)
{
	unsigned int path, group, count = 0;

	/* Original checks all16 entries together; individualff values remain
	 * signed-1 when any other entry is programmed. */
	for (path = 0; path < 2; path++)
		for (group = 0; group < 8; group++)
			count += (u8)value[path][group] == 255;
	*erased = count == 16;
	if (*erased)
		memset(value, 0, 16);
}

static inline int rtl_ax_trim_start(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_trim *trim)
{
	unsigned int i, path, group;
	int ret;

	/* Actual get_total_efuse_C@338810:590+50 bytes;
	 * XB@3620b8:5a0+40 bytes. PhysicalOTP, not logicalmap/HS. */
	trim->otp_start = chip == RTL_AX_8852C ? 0x590 : 0x5a0;
	trim->otp_bytes = chip == RTL_AX_8852C ? 80 : 64;
	for (i = 0; i < trim->otp_bytes; i++) {
		ret = rtl_ax_efuse_read(io, trim->otp_start + i, &trim->otp[i]);
		if (ret)
			return ret;
	}
	trim->thermal[0] = trim->otp[0x5df - trim->otp_start];
	trim->thermal[1] = trim->otp[0x5dc - trim->otp_start];
	trim->bias[0] = trim->otp[0x5de - trim->otp_start];
	trim->bias[1] = trim->otp[0x5db - trim->otp_start];
	/* Actual thermal_C@338974/XB@3621e8 rotates the low nibble,
	 * then writesRF43[19:16]. Both erased disables the pair. */
	if ((trim->thermal[0] & trim->thermal[1]) != 255) {
		for (path = 0; path < 2; path++) {
			u8 value = trim->thermal[path];

			trim->thermal_code[path] = ((value & 1) << 3) | ((value >> 1) & 7);
			ret = rtl_ax_phy_rf_write_masked(io, path, 0x43, 0xf0000, trim->thermal_code[path]);
			if (ret)
				return ret;
		}
		trim->thermal_written = true;
	}
	/* Actual PA_C@338b18 writes2G low and5G high nibble for A→B;
	 * XB@362344 only writesRF60[15:12], never its upper nibble. */
	if ((trim->bias[0] & trim->bias[1]) != 255) {
		for (path = 0; path < 2; path++) {
			ret = rtl_ax_phy_rf_write_masked(io, path, 0x60, 0xf000, trim->bias[path] & 15);
			if (ret)
				return ret;
			if (chip == RTL_AX_8852C) {
				ret = rtl_ax_phy_rf_write_masked(io, path, 0x60, 0xf0000, trim->bias[path] >> 4);
				if (ret)
					return ret;
			}
		}
		trim->bias_written = true;
	}
	/* Actual get_tssi_trim_C@338d10/XB@3624b0 stores reverseOTP groups.
	 * 2G/5G: A5d6..5cf,B5ab..5a4. C6G:A5ce..5c7,B5a3..59c. */
	for (path = 0; path < 2; path++)
		for (group = 0; group < 8; group++) {
			trim->tssi[path][group] = trim->otp[(path ? 0x5ab : 0x5d6) - group - trim->otp_start];
			if (chip == RTL_AX_8852C)
				trim->tssi_6g[path][group] = trim->otp[(path ? 0x5a3 : 0x5ce) - group - trim->otp_start];
		}
	rtl_ax_trim_tssi_erased(trim->tssi, &trim->tssi_erased);
	if (chip == RTL_AX_8852C)
		rtl_ax_trim_tssi_erased(trim->tssi_6g, &trim->tssi_6g_erased);
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_read(io, path, 0x43, &trim->rf43[path]);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_read(io, path, 0x60, &trim->rf60[path]);
		if (ret)
			return ret;
	}
	trim->ready = true;
	return 0;
}
#endif
