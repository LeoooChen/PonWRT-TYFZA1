/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_RX_CHECKSUM_H
#define RTL9617C_RX_CHECKSUM_H

/* TYFZA1 EPP bit5 is L4 checksum error. Only claim the IPv4 formats whose
 * good/bad checksums have been verified through the actual NI/FE parser. */
static inline bool rtl_rx_checksum_valid(unsigned int command,
	const unsigned char *frame, unsigned int bytes)
{
	const unsigned char *ip;
	unsigned int total, length;

	if ((command & (1U << 5)) || bytes < 42 ||
	    frame[12] != 8 || frame[13] != 0)
		return false;
	ip = frame + 14;
	if (ip[0] != 0x45 || (ip[6] & 0x3f) || ip[7])
		return false;
	total = (ip[2] << 8) | ip[3];
	if (total < 28 || total > bytes - 14)
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
