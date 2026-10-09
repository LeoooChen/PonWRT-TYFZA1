/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_LIMITS_H
#define RTL_AX_AP_LIMITS_H
#include <linux/types.h>

struct device;
/* Same record fields as the original9-byte limit/8-byte RU input. Mode
 * selects the separate6GHz LPI/SP/VLP tables;ru selects the RU26/52/106 axis. */
struct rtl_ax_limit_key { u8 band, bandwidth, ntx, rate, bf, channel, mode, ru; };
struct rtl_ax_limit_entry {
	struct rtl_ax_limit_key key;
	u8 regulation, shape;
	s8 power;
};
struct rtl_ax_limit_exception {
	char country[3];
	u16 domain;
	u8 regulation;
};
struct rtl_ax_limits {
	struct rtl_ax_limit_entry *entries;
	unsigned int count, ordinary_count, ru_count, files;
	char regulation[32][16];
	u8 regulations;
	struct rtl_ax_limit_exception exception[32];
	u8 exceptions;
};
int rtl_ax_limits_load(struct device *dev, unsigned int chip, unsigned int stage,
		       struct rtl_ax_limits *limits);
void rtl_ax_limits_release(struct rtl_ax_limits *limits);
int rtl_ax_limit_get(const struct rtl_ax_limits *limits, const struct rtl_ax_limit_key *key,
		     const char *regulation, s8 *power, u8 *shape);
#endif
