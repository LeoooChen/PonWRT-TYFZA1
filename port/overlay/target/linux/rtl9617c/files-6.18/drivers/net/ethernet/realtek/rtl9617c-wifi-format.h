/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Native FC/NE Wi-Fi lookup ABI, not the radio's PCIe WD format. */
#ifndef RTL9617C_WIFI_FORMAT_H
#define RTL9617C_WIFI_FORMAT_H

#define RTL_WIFI_SOURCE 18U
#define RTL_WIFI_ID_MAX 60U
#define RTL_WIFI_INTERNAL_BYTES 16U

/* HP=11, mode0, internal LAN connector25, bypass L2 before L3 lookup. The caller
 * supplies an allocated interface ID and an NI-owned Ethernet buffer. */
static inline unsigned int rtl_wifi_lookup_control(unsigned int id,
						 unsigned int length)
{
	return 0x81000001U | (RTL_WIFI_SOURCE << 25) | (length << 8) | (id << 1);
}

static inline unsigned int rtl_wifi_epp_id(unsigned int length_word)
{
	return (length_word >> 14) & 63;
}

/* QM crc_en=0: compact EPP length includes both internal eight-byte headers.
 * ID0 selects the ordinary Header A/CPU decoder; 61..63 are not netdev IDs. */
static inline int rtl_wifi_epp_length(unsigned int length_word,
				      unsigned int capacity)
{
	unsigned int id = rtl_wifi_epp_id(length_word);
	unsigned int size = length_word & 0x3fff;

	if (!id || id > RTL_WIFI_ID_MAX || size < RTL_WIFI_INTERNAL_BYTES + 14 ||
	    size - RTL_WIFI_INTERNAL_BYTES > capacity)
		return -1;
	return size - RTL_WIFI_INTERNAL_BYTES;
}

#endif
