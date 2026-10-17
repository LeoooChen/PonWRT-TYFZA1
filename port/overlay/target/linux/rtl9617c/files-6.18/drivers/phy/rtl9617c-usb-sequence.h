/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_USB_SEQUENCE_H
#define RTL9617C_USB_SEQUENCE_H

/* taurus_usb2_phy_port_cfg writes the address low/high nibbles in order. */
static inline void rtl_usb2_write(void *context,
		void (*write)(void *, unsigned int, unsigned int),
		unsigned int port, unsigned int address, unsigned int value)
{
	unsigned int word = value << 24 | (address & 15) << 20 | 0x64;
	unsigned int reg = port * 8;

	write(context, reg, word | 0x80000);
	write(context, reg, word);
	write(context, reg, word | 0x80000);
	word = value << 24 | (address >> 4) << 20 | 0x64;
	write(context, reg, word | 0x80000);
	write(context, reg, word);
	write(context, reg, word | 0x80000);
}

#endif
