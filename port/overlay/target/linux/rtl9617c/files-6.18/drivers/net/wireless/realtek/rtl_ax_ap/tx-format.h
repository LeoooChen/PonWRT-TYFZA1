/* SPDX-License-Identifier: GPL-2.0-only */
/* Actual original PCIe C/XB truncated WP format, independent of host SDK ABI. */
#ifndef RTL_AX_AP_TX_FORMAT_H
#define RTL_AX_AP_TX_FORMAT_H
#include <linux/minmax.h>
#include <linux/types.h>
#include "transport.h"

#define RTL_AX_TX_WP_SEQUENCE_BYTES 8
#define RTL_AX_TX_WP_ADDRESS_BYTES 6
#define RTL_AX_TX_WP_FRAGMENT_BYTES 2047
#define RTL_AX_TX_WP_BYTES 128
#define RTL_AX_TX_WP_ADDRESS_MAX ((RTL_AX_TX_WP_BYTES - 56 - RTL_AX_TX_WP_SEQUENCE_BYTES) / RTL_AX_TX_WP_ADDRESS_BYTES)

static inline unsigned int rtl_ax_tx_dma_channel(unsigned int category, unsigned int mac_band)
{
	/* Actual mapping@27c014/282af0: TID0..7,management8,high9.
	 * Both separate TYFZA1 radios currently operate MAC SCC band0. */
	static const u8 channels[] = { 0, 1, 1, 0, 2, 2, 3, 3, 8, 9 };

	return channels[category] + (mac_band ? category < 8 ? 4 : 2 : 0);
}

static inline void rtl_ax_tx_wp_sequence(u8 *out, u16 sequence)
{
	/* Actual fill_seq@27cfbc writes the first word of the eight-byte
	 * sequence region: preserve upper16, low15 sequence, valid15. */
	rtl_ax_store32(out, (rtl_ax_ap_read32(out) & 0xffff0000) | 0x8000 | (sequence & 0x7fff));
}

static inline void rtl_ax_tx_wp_address(u8 *out, u32 dma_address, unsigned int bytes, bool last)
{
	u16 field = (bytes & 0x7ff) | (last ? 0x8000 : 0);

	/* Actual fill_address@27cfd0: length11,last15,address32. No
	 * eight-byte address entry or full-width SDK address-info flags. */
	out[0] = field;
	out[1] = field >> 8;
	rtl_ax_store32(out + 2, dma_address);
}

static inline unsigned int rtl_ax_tx_wp_addresses(u8 *out, u32 dma_address, unsigned int bytes, bool last)
{
	unsigned int count = 0;

	/* One MPDU can span multiple original 11-bit subpacket entries. */
	while (bytes) {
		unsigned int fragment = min(bytes, (unsigned int)RTL_AX_TX_WP_FRAGMENT_BYTES);

		rtl_ax_tx_wp_address(out, dma_address, fragment, last && bytes == fragment);
		out += RTL_AX_TX_WP_ADDRESS_BYTES;
		dma_address += fragment;
		bytes -= fragment;
		count++;
	}
	return count;
}
#endif
