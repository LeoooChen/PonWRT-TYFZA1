/* SPDX-License-Identifier: GPL-2.0-only */
/* RFE54 original shape0 startup: actual C/XB BB functions and the
 * default bandedge/fast-mode host inputs; trace_stock_tx_shape.py. */
#ifndef RTL_AX_AP_TX_SHAPE_H
#define RTL_AX_AP_TX_SHAPE_H
#include "channel.h"
struct rtl_ax_tx_shape_op { u32 address, mask, value; bool mac; };
static const struct rtl_ax_tx_shape_op rtl_ax_shape_8852c_ofdm[] = {
	{ 0xd91c, 0xff000000, 0x00000000, true },
	{ 0xd920, 0x000000ff, 0x00000000, true },
	{ 0xd920, 0x0000ff00, 0x00000000, true },
	{ 0xd920, 0x00ff0000, 0x00000000, true },
	{ 0xd920, 0xff000000, 0x00000000, true },
	{ 0xd924, 0x000000ff, 0x00000000, true },
	{ 0xd924, 0x0000ff00, 0x00000000, true },
	{ 0xd914, 0xff000000, 0x00000000, true },
	{ 0xd918, 0x000000ff, 0x00000000, true },
	{ 0xd918, 0x0000ff00, 0x00000000, true },
	{ 0xd918, 0x00ff0000, 0x00000000, true },
	{ 0xd918, 0xff000000, 0x00000000, true },
	{ 0xd91c, 0x000000ff, 0x00000000, true },
	{ 0xd91c, 0x0000ff00, 0x00000000, true },
	{ 0xd91c, 0x00ff0000, 0x00000000, true },
	{ 0xd94c, 0xc0000000, 0x00000000, true },
	{ 0xd934, 0x00ff0000, 0x00000000, true },
	{ 0xd934, 0xff000000, 0x00000000, true },
	{ 0xd938, 0x000000ff, 0x00000000, true },
	{ 0xd934, 0x0000ff00, 0x00000000, true },
	{ 0x58f8, 0x80000000, 0x00000000, false },
	{ 0x78f8, 0x80000000, 0x00000000, false },
};
static const struct rtl_ax_tx_shape_op rtl_ax_shape_8192xb_ofdm[] = {
	{ 0xd91c, 0xff000000, 0x00000000, true },
	{ 0xd920, 0x000000ff, 0x00000000, true },
	{ 0xd920, 0x0000ff00, 0x00000000, true },
	{ 0xd920, 0x00ff0000, 0x00000000, true },
	{ 0xd920, 0xff000000, 0x00000000, true },
	{ 0xd924, 0x000000ff, 0x00000000, true },
	{ 0xd924, 0x0000ff00, 0x00000000, true },
	{ 0xd914, 0xff000000, 0x00000000, true },
	{ 0xd918, 0x000000ff, 0x00000000, true },
	{ 0xd918, 0x0000ff00, 0x00000000, true },
	{ 0xd918, 0x00ff0000, 0x00000000, true },
	{ 0xd918, 0xff000000, 0x00000000, true },
	{ 0xd91c, 0x000000ff, 0x00000000, true },
	{ 0xd91c, 0x0000ff00, 0x00000000, true },
	{ 0xd91c, 0x00ff0000, 0x00000000, true },
	{ 0xd94c, 0xc0000000, 0x00000000, true },
	{ 0xd934, 0x00ff0000, 0x00000000, true },
	{ 0xd934, 0xff000000, 0x00000000, true },
	{ 0xd938, 0x000000ff, 0x00000000, true },
	{ 0xd934, 0x0000ff00, 0x00000000, true },
	{ 0x56c4, 0x00000010, 0x00000001, false },
	{ 0x566c, 0x00200000, 0x00000001, false },
	{ 0x76c4, 0x00000010, 0x00000001, false },
	{ 0x766c, 0x00200000, 0x00000001, false },
};
static const struct rtl_ax_tx_shape_op rtl_ax_shape_8192xb_cck[] = {
	{ 0x45bc, 0x00ffffff, 0x003d23ff, false },
	{ 0x45cc, 0x00ffffff, 0x0029b354, false },
	{ 0x45d0, 0x00ffffff, 0x000fc1c8, false },
	{ 0x45d4, 0x00ffffff, 0x00fdb053, false },
	{ 0x45d8, 0x00ffffff, 0x00f86f9a, false },
	{ 0x45c0, 0x00ffffff, 0x00faef92, false },
	{ 0x45c4, 0x00ffffff, 0x00fe5fcc, false },
	{ 0x45c8, 0x00ffffff, 0x00ffdff5, false },
};
static inline void rtl_ax_tx_shape_zero(const struct rtl_ax_io *io, unsigned int chip)
{
	const struct rtl_ax_tx_shape_op *program;
	unsigned int count, i;

	if (chip == RTL_AX_8852C) {
		program = rtl_ax_shape_8852c_ofdm;
		count = ARRAY_SIZE(rtl_ax_shape_8852c_ofdm);
	} else {
		program = rtl_ax_shape_8192xb_ofdm;
		count = ARRAY_SIZE(rtl_ax_shape_8192xb_ofdm);
	}
	for (i = 0; i < count; i++) {
		const struct rtl_ax_tx_shape_op *op = &program[i];

		if (op->mac)
			rtl_ax_update32(io, op->address, op->mask, op->value << __ffs(op->mask));
		else
			rtl_ax_channel_field(io, op->address, op->mask, op->value);
	}
	if (chip == RTL_AX_8192XB)
		for (i = 0; i < ARRAY_SIZE(rtl_ax_shape_8192xb_cck); i++) {
			const struct rtl_ax_tx_shape_op *op = &rtl_ax_shape_8192xb_cck[i];

			rtl_ax_channel_field(io, op->address, op->mask, op->value);
		}
}
#endif
