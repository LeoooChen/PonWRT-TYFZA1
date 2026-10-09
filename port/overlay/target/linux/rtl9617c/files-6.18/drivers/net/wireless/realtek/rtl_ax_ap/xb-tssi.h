/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_XB_TSSI_H
#define RTL_AX_AP_XB_TSSI_H
#include <linux/firmware.h>
#include "board-cal.h"
#include "xb-tssi-program.h"

struct rtl_ax_xb_tssi {
	bool input_ready, ready;
	s8 curves[2][2][30];
	u8 thermal[2];
	u32 operations, thermal_readback[2][16], track_readback[2], RF10055[2];
};

static inline int rtl_ax_xb_tssi_load(struct device *dev, struct rtl_ax_xb_tssi *state)
{
	const struct firmware *fw;
	int ret = request_firmware(&fw, "realtek/tyfza1/rtl8192xbe/tssi-thermal-2g.bin", dev);

	if (ret)
		return ret;
	if (fw->size != sizeof(state->curves))
		ret = -EINVAL;
	else {
		memcpy(state->curves, fw->data, sizeof(state->curves));
		state->input_ready = true;
	}
	release_firmware(fw);
	return ret;
}

static inline void rtl_ax_xb_tssi_meter(const struct rtl_ax_io *io,
	const struct rtl_ax_board_cal *cal, unsigned int path, struct rtl_ax_xb_tssi *state)
{
	s8 table[64] = {};
	u32 offset = path * 0x2000;
	u8 thermal = cal->thermal[path], reference = thermal == 255 ? 32 : thermal & 63;
	unsigned int i, j;

	/* Original meter@35c4e8 normaltype,HS reference and actual
	 * base-then-FEM curves. Saturate the 30-point table as original. */
	state->thermal[path] = thermal;
	if (thermal != 255) {
		for (i = 0; i < 32; i++)
			table[i] = -state->curves[path][1][min(i, 29U)];
		for (i = 63, j = 1; i >= 32; i--, j++)
			table[i] = state->curves[path][0][min(j, 29U)];
	}
	rtl_ax_channel_field(io, 0x5810 + offset, BIT(16), 0);
	rtl_ax_channel_field(io, 0x5810 + offset, BIT(24), 1);
	rtl_ax_channel_field(io, 0x5810 + offset, 0xfc00, reference);
	rtl_ax_channel_field(io, 0x5864 + offset, 0x03f00000, reference);
	for (i = 0; i < 16; i++) {
		u32 value = (u8)table[i * 4] | (u32)(u8)table[i * 4 + 1] << 8 |
			(u32)(u8)table[i * 4 + 2] << 16 | (u32)(u8)table[i * 4 + 3] << 24;

		io->write32(io->ctx, (0x5c00 + offset + i * 4) | 0x10000, value);
		state->thermal_readback[path][i] = io->read32(io->ctx, (0x5c00 + offset + i * 4) | 0x10000);
	}
	rtl_ax_channel_field(io, 0x5864 + offset, BIT(26), 1);
	rtl_ax_channel_field(io, 0x5864 + offset, BIT(26), 0);
	state->operations += 22;
}

static inline int rtl_ax_xb_tssi_start(const struct rtl_ax_io *io,
	const struct rtl_ax_board_cal *cal, struct rtl_ax_xb_tssi *state)
{
	unsigned int step, i, path;
	int ret;

	state->ready = false;
	state->operations = 0;
	/* Native register tables extracted from this board's actual ELF,
	 * not the original host executable. Original do_tssi@361060:
	 * disable,A all stages,B all stages,enable; DE follows in caller. */
	for (step = 0; step < ARRAY_SIZE(rtl_ax_xb_tssi_steps); step++) {
		const struct rtl_ax_tssi_step *program = &rtl_ax_xb_tssi_steps[step];

		if (program->thermal_path >= 0) {
			rtl_ax_xb_tssi_meter(io, cal, program->thermal_path, state);
			continue;
		}
		for (i = 0; i < program->count; i++) {
			const struct rtl_ax_tssi_op *op = &program->ops[i];

			if (op->rf_path == 255)
				rtl_ax_channel_field(io, op->address, op->mask, op->value);
			else {
				ret = rtl_ax_phy_rf_write_masked(io, op->rf_path, op->address, op->mask, op->value);
				if (ret)
					return ret;
			}
			state->operations++;
		}
	}
	for (path = 0; path < 2; path++) {
		state->track_readback[path] = io->read32(io->ctx, (0x5818 + path * 0x2000) | 0x10000);
		ret = rtl_ax_phy_rf_read(io, path, 0x10055, &state->RF10055[path]);
		if (ret)
			return ret;
	}
	state->ready = true;
	return 0;
}
#endif
