/* SPDX-License-Identifier: GPL-2.0-only */
/* TYFZA1 mac_send_bcn_h2c@20d668: 16-byte header followed by raw beacon. */
#ifndef RTL_AX_AP_BEACON_H
#define RTL_AX_AP_BEACON_H
#include "transport.h"

static inline void rtl_ax_beacon_header(unsigned char out[16],
	unsigned int tim_offset, unsigned int rate, unsigned int antenna)
{
	memset(out, 0, 16);
	/* TIM is relative to the end of the 24-byte 802.11 header, as in
	 * rtw_core_issue_beacon. Hardware updates TIM and sequence. */
	rtl_ax_store32(out, (tim_offset | 128U) << 24);
	rtl_ax_store32(out + 4, (1U << 8) | (1U << 10) | rate << 12 | (1U << 24));
	/* Original mac_send_bcn_h2c byte11 enables the optional antenna
	 * override: path bitmap in12, NSTS0/1 maps in13/14. */
	if (antenna)
		rtl_ax_store32(out + 8, 1 | antenna << 1 |
			(antenna == 2 ? 1U << 5 : 0) | (antenna == 3 ? 1U << 7 : 0));
}
#endif
