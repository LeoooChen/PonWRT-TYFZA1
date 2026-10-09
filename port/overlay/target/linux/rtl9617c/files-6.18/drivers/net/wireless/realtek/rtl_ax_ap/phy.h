/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_PHY_H
#define RTL_AX_AP_PHY_H
#include <linux/types.h>
#include "power.h"

struct device;

enum rtl_ax_phy_stage { RTL_AX_PHY_BASE, RTL_AX_PHY_FEM, RTL_AX_PHY_STAGES };
enum rtl_ax_phy_table_id {
	RTL_AX_PHY_REG, RTL_AX_PHY_GAIN, RTL_AX_RADIO_A, RTL_AX_RADIO_B,
	RTL_AX_PHY_TABLES
};

struct rtl_ax_phy_pair { u32 address, value; };
struct rtl_ax_phy_table { struct rtl_ax_phy_pair *pairs; unsigned int count; };
struct rtl_ax_phy_inputs {
	struct rtl_ax_phy_table table[RTL_AX_PHY_STAGES][RTL_AX_PHY_TABLES];
	unsigned int chip;
};

/* Original RF radio synchronization uses H2CB_LONG_DATA,category OUTSRC,
 * class8(A)/9(B),function=page,at most500 little-endian words per page. */
struct rtl_ax_phy_commands {
	void *ctx;
	int (*send_radio)(void *ctx, unsigned int class, unsigned int page,
			  const __le32 *words, unsigned int bytes);
};

int rtl_ax_phy_load(struct device *dev, unsigned int chip, struct rtl_ax_phy_inputs *inputs);
void rtl_ax_phy_release(struct rtl_ax_phy_inputs *inputs);
void rtl_ax_phy_bb_apply(const struct rtl_ax_io *io, const struct rtl_ax_phy_inputs *inputs,
			enum rtl_ax_phy_stage stage);
int rtl_ax_phy_radio_apply(const struct rtl_ax_io *io,
			   const struct rtl_ax_phy_inputs *inputs, enum rtl_ax_phy_stage stage,
			   const struct rtl_ax_phy_commands *commands);
#endif
