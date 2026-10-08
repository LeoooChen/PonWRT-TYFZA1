/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_MODE_H
#define RTL9617C_PON_MODE_H
enum rtl_pon_mode {
	RTL_PON_EPON_1G,
	RTL_PON_EPON_D10G,
	RTL_PON_EPON_BI10G,
	RTL_PON_GPON,
	RTL_PON_XGPON,
	RTL_PON_XGSPON,
	RTL_PON_MODE_COUNT,
};

struct rtl_pon_mode_configuration {
	const char *name;
	const char *profile;
	unsigned int sds_mode;
	unsigned int burst_page;
	unsigned int epon_onu_mode;
};

static const struct rtl_pon_mode_configuration rtl_pon_modes[] = {
	{"epon", "realtek/rtl9617c/pon_serdes_epon.bin", 0x40c, 62, 1},
	{"epon-d10g", "realtek/rtl9617c/pon_serdes_epon_d10g.bin", 0x44c, 60, 2},
	{"epon-bi10g", "realtek/rtl9617c/pon_serdes_epon_bi10g.bin", 0x46c, 60, 3},
	{"gpon", "realtek/rtl9617c/pon_serdes_gpon.bin", 0x408, 61, 0},
	{"xgpon", "realtek/rtl9617c/pon_serdes_xgpon.bin", 0x448, 45, 0},
	{"xgspon", "realtek/rtl9617c/pon_serdes_xgspon.bin", 0x428, 45, 0},
};

static inline int rtl_pon_mode_is_epon(enum rtl_pon_mode mode)
{
	return mode <= RTL_PON_EPON_BI10G;
}
#endif
