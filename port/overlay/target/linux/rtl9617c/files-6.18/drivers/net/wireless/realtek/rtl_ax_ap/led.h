/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_LED_H
#define RTL_AX_LED_H

/* Both original chips route software LED0 to GPIO8 in push-pull mode. */
static inline void rtl_ax_led_init(const struct rtl_ax_io *io)
{
	rtl_ax_update8(io, 0x2d4, 0x0f, 0x0f);
	rtl_ax_update16(io, 0x62, 0x0101, 0x0101);
}

static inline void rtl_ax_led_output(const struct rtl_ax_io *io, bool on)
{
	rtl_ax_update16(io, 0x62, 0x0101, 0x0101);
	rtl_ax_update8(io, 0x61, 1, !on);
}

#endif
