/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_BOARD_CAL_H
#define RTL_AX_AP_BOARD_CAL_H
#include <linux/types.h>
#include <linux/firmware.h>
#include <linux/string.h>
#include "power.h"

/* Native container of the board's exportedHS fields. OTP hidden trim is
 * read separately using the original physicalefuse transaction. */
struct rtl_ax_board_cal {
	u8 magic[8];
	u8 gain_2g5g[5], thermal[2], xtal;
	s8 tssi_cck[2][6], tssi_2g[2][5], tssi_5g[2][14];
} __packed;

static inline int rtl_ax_board_cal_load(struct device *dev, unsigned int chip,
				       struct rtl_ax_board_cal *calibration)
{
	const struct firmware *fw;
	const char *name = chip == RTL_AX_8852C ?
		"realtek/tyfza1/rtl8852ce/flash-calibration.bin" :
		"realtek/tyfza1/rtl8192xbe/flash-calibration.bin";
	int ret = request_firmware(&fw, name, dev);

	if (ret)
		return ret;
	if (fw->size != sizeof(*calibration) || memcmp(fw->data, "TYFC0001", 8))
		ret = -EINVAL;
	else
		memcpy(calibration, fw->data, sizeof(*calibration));
	release_firmware(fw);
	return ret;
}
#endif
