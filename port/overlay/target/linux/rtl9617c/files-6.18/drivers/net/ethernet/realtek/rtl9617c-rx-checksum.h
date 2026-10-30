/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_RX_CHECKSUM_H
#define RTL9617C_RX_CHECKSUM_H

/* TYFZA1 EPP bit5 is L4 checksum error. Only claim the IPv4 formats whose
 * good/bad checksums have been verified through the actual NI/FE parser. */
static inline bool rtl_rx_checksum_valid(unsigned int command,
	const unsigned char *frame, unsigned int bytes)
{
	const unsigned char *ip;
	unsigned int total, length, offset = 14, ppp_length = 0;

	if ((command & (1U << 5)) || bytes < 42)
		return false;
	if (frame[12] == 0x88 && frame[13] == 0x64) {
		/* FE PPPoE insertion preserves the verified inner L4 result. */
		if (bytes < 50 || frame[14] != 0x11 || frame[15] ||
		    frame[20] != 0 || frame[21] != 0x21)
			return false;
		ppp_length = (frame[18] << 8) | frame[19];
		offset = 22;
	} else if (frame[12] != 8 || frame[13] != 0) {
		return false;
	}
	ip = frame + offset;
	if (ip[0] != 0x45 || (ip[6] & 0x3f) || ip[7])
		return false;
	total = (ip[2] << 8) | ip[3];
	if (total < 28 || total > bytes - offset ||
	    (offset == 22 && ppp_length != total + 2))
		return false;
	length = total - 20;
	if (ip[9] == 6)
		return length >= 20 && (ip[32] >> 4) >= 5 &&
		       (ip[32] >> 4) * 4 <= length;
	if (ip[9] == 17)
		return (((unsigned int)ip[24] << 8) | ip[25]) == length && (ip[26] || ip[27]);
	return false;
}
#endif
