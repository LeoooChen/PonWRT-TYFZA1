/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RF_START_H
#define RTL_AX_AP_RF_START_H
#include "power.h"
#include "fw.h"
#include "phy.h"
#include <linux/firmware.h>

struct rtl_ax_rf_start {
	bool crystal_ready, gpio_ready, nctl_ready, rck_ready;
	u8 crystal_requested, crystal_observed[2], share_control;
	u32 nctl_pairs, nctl_wait_observations, nctl_wait_value;
	u8 rck_code[2], rck_observations[2];
	bool rck_done[2];
};

static inline void rtl_ax_rf_gpio_pin(const struct rtl_ax_io *io,
	unsigned int gpio, unsigned int path, bool inverted, unsigned int source)
{
	unsigned int base = path ? 0x17800 : 0x15800;
	unsigned int shift = (gpio & 7) * 4;

	/* Actual halbb_gpio_setting_C@2e5400/XB: two-bit path in033c,
	 * inversion bit in5890/7890,source nibble in5880/7880 onwards. */
	rtl_ax_update32(io, 0x1033c, 3U << (gpio * 2), path << (gpio * 2));
	rtl_ax_update32(io, base + 0x90, BIT(gpio), inverted ? BIT(gpio) : 0);
	rtl_ax_update32(io, base + 0x80 + (gpio / 8) * 4, 15U << shift, source << shift);
}

static inline void rtl_ax_rf_gpio_mux(const struct rtl_ax_io *io, unsigned int gpio)
{
	unsigned int shift = (gpio & 1) * 4;

	/* Actual MAC func19 records from C pointer table.rodata+1f820 and
	 * XB+21508 (ELF relocations resolved). All board RFE54 pins select8. */
	rtl_ax_update8(io, 0x2d0 + gpio / 2, 15U << shift, 8U << shift);
}

static inline void rtl_ax_rf_gpio_start(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_rf_start *rf)
{
	static const u8 mac_pins_C[6] = { 1, 2, 3, 6, 7, 10 };
	static const u8 mac_pins_XB[6] = { 1, 2, 3, 6, 7, 11 };
	const u8 *pins;
	unsigned int i;

	/* Exact board RFE54 branches set_gpio_C@30f69c/XB@33e32c. */
	if (chip == RTL_AX_8852C) {
		rtl_ax_rf_gpio_pin(io, 10, 0, true, 4);
		rtl_ax_rf_gpio_pin(io, 7, 0, true, 3);
		rtl_ax_rf_gpio_pin(io, 6, 0, false, 0);
		rtl_ax_rf_gpio_pin(io, 1, 1, true, 4);
		rtl_ax_rf_gpio_pin(io, 2, 1, true, 3);
		rtl_ax_rf_gpio_pin(io, 3, 1, false, 0);
		pins = mac_pins_C;
	} else {
		rtl_ax_rf_gpio_pin(io, 11, 0, false, 0);
		rtl_ax_rf_gpio_pin(io, 7, 0, true, 4);
		rtl_ax_rf_gpio_pin(io, 6, 0, true, 3);
		rtl_ax_rf_gpio_pin(io, 1, 1, false, 0);
		rtl_ax_rf_gpio_pin(io, 2, 1, true, 4);
		rtl_ax_rf_gpio_pin(io, 3, 1, true, 3);
		pins = mac_pins_XB;
	}
	for (i = 0; i < 6; i++)
		rtl_ax_rf_gpio_mux(io, pins[i]);
	/* The original eFEM diversity output is C11/XB12, inverted ZERO7. */
	i = chip == RTL_AX_8852C ? 11 : 12;
	rtl_ax_rf_gpio_pin(io, i, 0, true, 7);
	rtl_ax_rf_gpio_mux(io, i);
	rf->gpio_ready = true;
}

static inline int rtl_ax_rf_crystal_start(const struct rtl_ax_io *io,
	unsigned int chip, bool shared, u8 flash_cap, struct rtl_ax_rf_start *rf)
{
	int ret;

	/* Actual halrf_set_share_xtal_C@30fd14/XB@33f8fc, first RF DM
	 * operation. C clears8[1:0] when not shared and always uses flash
	 * xcap; XB uses127 when not shared,flash xcap only when shared. */
	if (chip == RTL_AX_8852C && !shared) {
		ret = rtl_ax_xtal_write(io, 8, 0, 3);
		if (ret)
			return ret;
	}
	rf->crystal_requested = chip == RTL_AX_8192XB && !shared ? 127 : flash_cap;
	ret = rtl_ax_xtal_write(io, 4, rf->crystal_requested, 127);
	if (ret)
		return ret;
	ret = rtl_ax_xtal_write(io, 5, rf->crystal_requested, 127);
	if (ret)
		return ret;
	ret = rtl_ax_xtal_read(io, 4, &rf->crystal_observed[0]);
	if (ret)
		return ret;
	ret = rtl_ax_xtal_read(io, 5, &rf->crystal_observed[1]);
	if (ret)
		return ret;
	ret = rtl_ax_xtal_read(io, 8, &rf->share_control);
	if (ret)
		return ret;
	rf->crystal_ready = true;
	return 0;
}

static inline int rtl_ax_rf_nctl_start(struct device *dev, const struct rtl_ax_io *io,
	unsigned int chip, unsigned int hardware_cv, struct rtl_ax_rf_start *rf)
{
	static const u32 delay_us[6] = { 1, 5, 50, 1000, 5000, 50000 };
	const struct firmware *firmware;
	const char *name;
	unsigned int pairs, i;
	int ret;

	if (chip == RTL_AX_8852C) {
		name = hardware_cv ? "realtek/tyfza1/array_mp_8852c_nctl_reg.bin" :
			"realtek/tyfza1/array_mp_8852c_nctl_reg_a.bin";
		pairs = hardware_cv ? 1782 : 1769;
	} else {
		name = "realtek/tyfza1/array_mp_8192xb_nctl_reg.bin";
		pairs = 1421;
	}
	ret = request_firmware(&firmware, name, dev);
	if (ret)
		return ret;
	if (firmware->size != pairs * 8) {
		release_firmware(firmware);
		return -EINVAL;
	}
	/* Exact config_nctl_C@3106f8/XB@33ffb0 preamble. The board
	 * continues after1000 unsuccessful reset observations. */
	rtl_ax_update32(io, 0x10c60, 3, 3);
	rtl_ax_update32(io, 0x10c6c, 0, 1);
	rtl_ax_update32(io, 0x158ac, 0, BIT(27));
	rtl_ax_update32(io, 0x178ac, 0, BIT(27));
	io->write32(io->ctx, 0x18000, 8);
	for (i = 0; i < 1000; i++) {
		io->write32(io->ctx, 0x18080, 4);
		io->delay_us(io->ctx, 1);
		rf->nctl_wait_value = io->read32(io->ctx, 0x18080);
		rf->nctl_wait_observations++;
		if (rf->nctl_wait_value == 4)
			break;
	}
	for (i = 0; i < pairs; i++) {
		u32 address = rtl_ax_ap_read32(firmware->data + i * 8);
		u32 value = rtl_ax_ap_read32(firmware->data + i * 8 + 4);

		/* Actual cfg_rf_nctl_C@31bd44/XB@344fe0 delay opcodes.
		 * They are not present in the three board arrays, but belong
		 * to the parameter format shared with the original callback. */
		if (address >= 0xf9 && address <= 0xfe)
			io->delay_us(io->ctx, delay_us[address - 0xf9]);
		else
			io->write32(io->ctx, address | 0x10000, value);
		rf->nctl_pairs++;
	}
	release_firmware(firmware);
	rf->nctl_ready = true;
	return 0;
}

static inline int rtl_ax_rf_rck_start(const struct rtl_ax_io *io, struct rtl_ax_rf_start *rf)
{
	unsigned int path, attempt;
	u32 value;
	int ret;

	/* Actual rck_trigger@2f6654 always runs path A then B. The C
	 * primitive@30cc44 and XB@33c69c have identical hardware steps. */
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 0);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0, 0xf0000, 3);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x1b, 0xfffff, 0x240);
		if (ret)
			return ret;
		/* First observation is immediate,up to11 reads and10 delays2us.
		 * Original still extracts and installs the result after the limit. */
		for (attempt = 0; attempt <= 10; attempt++) {
			ret = rtl_ax_phy_rf_read(io, path, 0x1c, &value);
			if (ret)
				return ret;
			rf->rck_observations[path]++;
			rf->rck_done[path] = !!(value & 8);
			if (rf->rck_done[path] || attempt == 10)
				break;
			io->delay_us(io->ctx, 2);
		}
		ret = rtl_ax_phy_rf_read(io, path, 0x1b, &value);
		if (ret)
			return ret;
		rf->rck_code[path] = (value >> 10) & 31;
		ret = rtl_ax_phy_rf_write_masked(io, path, 0x1b, 0xfffff, rf->rck_code[path]);
		if (ret)
			return ret;
		ret = rtl_ax_phy_rf_write_masked(io, path, 5, 0xfffff, 1);
		if (ret)
			return ret;
	}
	rf->rck_ready = true;
	return 0;
}
#endif
