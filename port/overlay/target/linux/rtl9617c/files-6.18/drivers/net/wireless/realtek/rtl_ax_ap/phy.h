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
/* Actual board gain callback accepts bands0..7 and paths0..1;the newer
 * SDK's12-band host layout is not the original module's layout. */
struct rtl_ax_phy_gain {
	s8 lna[8][2][7], tia[8][2][2], lna_bypass[8][2][7];
	s8 lna_op1db[8][2][7], tia_lna_op1db[8][2][8];
	s8 rpl20[8][2], rpl40[8][2][9], rpl80[8][2][13], rpl160[8][2][15];
	u32 wb_elna[8][2], gs[8][2][2];
	u8 wb_lna_tia[8][2][16], elna[8][2][2];
};
struct rtl_ax_phy_inputs {
	struct rtl_ax_phy_table table[RTL_AX_PHY_STAGES][RTL_AX_PHY_TABLES];
	struct rtl_ax_phy_gain gain[RTL_AX_PHY_STAGES];
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
void rtl_ax_phy_bb_reset(const struct rtl_ax_io *io, unsigned int chip);
int rtl_ax_phy_radio_apply(const struct rtl_ax_io *io,
			   const struct rtl_ax_phy_inputs *inputs, enum rtl_ax_phy_stage stage,
			   const struct rtl_ax_phy_commands *commands);
#endif
