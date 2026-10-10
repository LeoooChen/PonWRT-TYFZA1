/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_C_TSSI_H
#define RTL_AX_AP_C_TSSI_H
#include <linux/firmware.h>
#include "board-cal.h"
#include "c-tssi-program.h"

struct rtl_ax_c_tssi {
	bool input_ready, ready, aligned[2];
	s8 curves[3][2][2][30];
	u8 thermal[2], channel, polls[2][4];
	u16 cw[2][4];
	s32 correction[2][2];
	u16 before[2][6], expected[2][6], readback[2][6];
	u32 operations, thermal_readback[2][16], RF10055[2], cleanup[2][4];
};

static inline int rtl_ax_c_tssi_load(struct device *dev, struct rtl_ax_c_tssi *state)
{
	const struct firmware *fw;
	int ret = request_firmware(&fw, "realtek/tyfza1/rtl8852ce/tssi-thermal-5g.bin", dev);

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

static inline void rtl_ax_c_tssi_meter(const struct rtl_ax_io *io,
	const struct rtl_ax_board_cal *cal, unsigned int path, struct rtl_ax_c_tssi *state)
{
	s8 table[64] = {};
	u32 offset = path * 0x2000;
	u8 thermal = cal->thermal[path], reference = thermal == 255 ? 32 : thermal & 63;
	unsigned int group = state->channel <= 64 ? 0 : state->channel <= 144 ? 1 : 2;
	unsigned int i, j;

	/* Actual meter@32dc38: 5G group [36,64]/[100,144]/[149,177],
	 * HS thermal reference, base overwritten by this board's BPF FEM. */
	state->thermal[path] = thermal;
	if (thermal != 255) {
		for (i = 0; i < 32; i++)
			table[i] = -state->curves[group][path][1][min(i, 29U)];
		for (i = 63, j = 1; i >= 32; i--, j++)
			table[i] = state->curves[group][path][0][min(j, 29U)];
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

static inline int rtl_ax_c_tssi_measure(const struct rtl_ax_io *io,
	unsigned int path, unsigned int point, struct rtl_ax_c_tssi *state)
{
	u32 offset = path * 0x2000, result;
	unsigned int i;

	rtl_ax_channel_field(io, 0x5820 + offset, BIT(31), 0);
	rtl_ax_channel_field(io, 0x5820 + offset, BIT(31), 1);
	io->delay_us(io->ctx, 100);
	/* Actual finish@332f78: read at 500,...,5000; delay only
	 * between failed reads. Result is the hardware 10-bit codeword. */
	for (i = 0; i < 10; i++) {
		result = io->read32(io->ctx, (0x1c18 + offset) | 0x10000);
		state->polls[path][point] = i + 1;
		if (result & BIT(16)) {
			state->cw[path][point] = io->read32(io->ctx, (0x1c18 + offset) | 0x10000) & 0x3ff;
			return 0;
		}
		if (i != 9)
			io->delay_us(io->ctx, 500);
	}
	return -ETIMEDOUT;
}

static inline void rtl_ax_c_tssi_correct(const struct rtl_ax_io *io,
	unsigned int path, unsigned int slot, u32 address, u32 mask,
	int correction, struct rtl_ax_c_tssi *state)
{
	u32 shift = __ffs(mask), value = io->read32(io->ctx, (address + path * 0x2000) | 0x10000);
	int adjusted;

	state->before[path][slot] = (value & mask) >> shift;
	adjusted = sign_extend32(state->before[path][slot], 9) + correction;
	/* Original signed 10-bit alignment saturation, then field backfill. */
	state->expected[path][slot] = clamp(adjusted, -512, 511) & 0x3ff;
	rtl_ax_channel_field(io, address + path * 0x2000, mask, state->expected[path][slot]);
	state->readback[path][slot] =
		(io->read32(io->ctx, (address + path * 0x2000) | 0x10000) & mask) >> shift;
}

static inline void rtl_ax_c_tssi_tone_stop(const struct rtl_ax_io *io,
	unsigned int path, struct rtl_ax_c_tssi *state)
{
	u32 offset = path * 0x2000;

	/* Actual successful alignment tail@334260, not the SDK variant. */
	rtl_ax_channel_field(io, 0x5680 + offset, BIT(3), 0);
	rtl_ax_channel_field(io, 0x5684 + offset, BIT(7), 1);
	rtl_ax_channel_field(io, 0x5684 + offset, BIT(7), 0);
	rtl_ax_channel_field(io, 0x5864 + offset, BIT(27), 1);
	rtl_ax_channel_field(io, 0x56cc + offset, BIT(28), 0);
	rtl_ax_channel_field(io, 0x033c, 0xff000000, 0x1f);
	rtl_ax_channel_field(io, 0x023c, 0xffff0000, 0x2001);
	rtl_ax_channel_field(io, 0x5670 + offset, BIT(0), 1);
	rtl_ax_channel_field(io, 0x02fc, BIT(16 + path), 1);
	rtl_ax_channel_field(io, 0x02fc, BIT(20 + path), 0);
	/* Original successful tail pulses the DAC path before releasing it;
	 * a final clear alone omits this hardware transition. */
	rtl_ax_channel_field(io, 0x023c, 0xffff0000, 0x2001);
	rtl_ax_channel_field(io, 0x5670 + offset, BIT(0), 1);
	rtl_ax_channel_field(io, 0x02fc, BIT(16 + path), 1);
	rtl_ax_channel_field(io, 0x02fc, BIT(20 + path), 0);
	rtl_ax_channel_field(io, 0x5670 + offset, BIT(0), 0);
	rtl_ax_channel_field(io, 0x02fc, BIT(16 + path), 0);
	state->cleanup[path][0] = io->read32(io->ctx, (0x5680 + offset) | 0x10000);
	state->cleanup[path][1] = io->read32(io->ctx, (0x5684 + offset) | 0x10000);
	state->cleanup[path][2] = io->read32(io->ctx, (0x5864 + offset) | 0x10000);
	state->cleanup[path][3] = io->read32(io->ctx, (0x56cc + offset) | 0x10000);
}

static inline int rtl_ax_c_tssi_align(const struct rtl_ax_io *io,
	unsigned int path, struct rtl_ax_c_tssi *state)
{
	static const s16 power[] = { 52, 24, 8, -8 };
	u32 offset = path * 0x2000;
	unsigned int point;
	int ret = 0, correction;

	/* Actual RFE54 5G alignment@333c04 uses the 5G array at sp+136.
	 * CV1 skips SW_DCK; each RFK trigger requests alignment (force=1). */
	state->aligned[path] = false;
	memset(state->polls[path], 0, sizeof(state->polls[path]));
	rtl_ax_channel_field(io, 0x2008, 0x01ffffff, 0xfffff);
	rtl_ax_channel_field(io, 0x5688 + offset, 0x03ff0000, 0x3a1);
	rtl_ax_channel_field(io, 0x5680 + offset, 0x000ffff0, 0);
	rtl_ax_channel_field(io, 0x5680 + offset, 0x07f00000, 0x7f);
	rtl_ax_channel_field(io, 0x5680 + offset, 0xf0000000, 2);
	rtl_ax_channel_field(io, 0x5680 + offset, BIT(27), 0);
	rtl_ax_channel_field(io, 0x5684 + offset, 0x7f, 1);
	rtl_ax_channel_field(io, 0x5684 + offset, 0x007f0000, 8);
	rtl_ax_channel_field(io, 0x5688 + offset, BIT(30), 1);
	rtl_ax_channel_field(io, 0x120c + offset, 0xf000, 1);
	rtl_ax_channel_field(io, 0x56cc + offset, BIT(28), 1);
	rtl_ax_channel_field(io, 0x56cc + offset, 0x0ff80000, power[0]);
	rtl_ax_channel_field(io, 0x5864 + offset, BIT(27), 0);
	rtl_ax_channel_field(io, 0x5680 + offset, BIT(2), 1);
	rtl_ax_channel_field(io, 0x5680 + offset, BIT(2), 0);
	io->delay_us(io->ctx, 2000);
	for (point = 0; point < 4; point++) {
		if (point)
			rtl_ax_channel_field(io, 0x56cc + offset, 0x0ff80000, power[point] & 0x1ff);
		ret = rtl_ax_c_tssi_measure(io, path, point, state);
		if (ret)
			break;
		if (point == 1) {
			correction = state->cw[path][0] - state->cw[path][1] - (power[0] - power[1]) * 2;
			state->correction[path][0] = correction;
			rtl_ax_c_tssi_correct(io, path, 0, 0x5638, 0x3ff, correction, state);
			rtl_ax_c_tssi_correct(io, path, 1, 0x5644, 0x3ff, correction, state);
		} else if (point == 3) {
			correction = state->cw[path][2] - state->cw[path][3] - (power[2] - power[3]) * 2;
			state->correction[path][1] = correction;
			rtl_ax_c_tssi_correct(io, path, 2, 0x5634, 0x3ff00000, correction, state);
			rtl_ax_c_tssi_correct(io, path, 3, 0x5640, 0x3ff00000, correction, state);
			rtl_ax_c_tssi_correct(io, path, 4, 0x5634, 0x000ffc00, correction, state);
			rtl_ax_c_tssi_correct(io, path, 5, 0x5640, 0x000ffc00, correction, state);
		}
	}
	/* Finish this calibration's single-tone transmission before returning. */
	rtl_ax_c_tssi_tone_stop(io, path, state);
	state->aligned[path] = !ret;
	return ret;
}

static inline int rtl_ax_c_tssi_start20(const struct rtl_ax_io *io,
	const struct rtl_ax_board_cal *cal, unsigned int channel, struct rtl_ax_c_tssi *state)
{
	unsigned int step, i, path;
	int ret;

	state->ready = false;
	state->channel = channel;
	state->operations = 0;
	/* Actual do_tssi@336df0: disable, all A stages including alignment,
	 * all B stages including alignment, enable; caller installs DE. */
	for (step = 0; step < ARRAY_SIZE(rtl_ax_c_tssi_steps); step++) {
		const struct rtl_ax_c_tssi_step *program = &rtl_ax_c_tssi_steps[step];

		if (program->thermal_path >= 0) {
			rtl_ax_c_tssi_meter(io, cal, program->thermal_path, state);
			continue;
		}
		if (program->alignment_path >= 0) {
			ret = rtl_ax_c_tssi_align(io, program->alignment_path, state);
			if (ret)
				return ret;
			continue;
		}
		for (i = 0; i < program->count; i++) {
			const struct rtl_ax_c_tssi_op *op = &program->ops[i];

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
		ret = rtl_ax_phy_rf_read(io, path, 0x10055, &state->RF10055[path]);
		if (ret)
			return ret;
	}
	state->ready = true;
	return 0;
}
#endif
