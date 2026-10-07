/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_XRAM_FORMAT_H
#define RTL9617C_XRAM_FORMAT_H

#define RTL_XRAM_RX_END 0x300
#define RTL_XRAM_TX_START 0x300
#define RTL_XRAM_TX_END 0x400

/* XRAM pointers count 8-byte cells; packet data uses 32-bit MMIO accesses. */
static inline unsigned int rtl_xram_tx_next(unsigned int pointer,
					    unsigned int wire_length)
{
	unsigned int cells = 1 + (8 + wire_length + 7) / 8;

	return RTL_XRAM_TX_START +
	       ((pointer - RTL_XRAM_TX_START + cells) %
		(RTL_XRAM_TX_END - RTL_XRAM_TX_START));
}

/* Frame length includes FCS. XR.bytes_valid describes the final partial cell. */
static inline unsigned int rtl_xram_wire_length(unsigned int length)
{
	return length < 60 ? 64 : length + 4;
}

/* TYFZA1 wire captures put valid-byte count at bits 14:12, not 13:10. */
static inline unsigned int rtl_xram_tx_header(unsigned int next,
					      unsigned int wire_length)
{
	return 0x80000000U | ((wire_length & 7) << 12) | next;
}

static inline unsigned int rtl_xram_word_next(unsigned int offset,
					      unsigned int base,
					      unsigned int end)
{
	return offset + 4 == end ? base : offset + 4;
}

#endif
