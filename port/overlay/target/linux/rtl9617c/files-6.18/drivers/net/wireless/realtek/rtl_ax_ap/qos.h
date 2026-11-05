/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_QOS_H
#define RTL_AX_QOS_H
/* tos_to_up@de93c: ordinary AP defaults (force_qos, custom DSCP and
 * CTC mapping disabled). The original remaps the eight priorities to
 * four TIDs even though its generic classify8021d callback does not. */
static inline unsigned int rtl_ax_ethernet_tid(const unsigned char *packet,
	unsigned int bytes)
{
	static const unsigned char tid[8] = {0, 1, 1, 0, 4, 4, 6, 6};
	unsigned int protocol, offset = 14, tos;
	if (bytes < 16) return 0;
	protocol = packet[12] << 8 | packet[13];
	if (protocol == 0x8864) {
		if (bytes < 24) return 0;
		protocol = packet[20] << 8 | packet[21];
		if (protocol == 0x21) protocol = 0x0800;
		else if (protocol == 0x57) protocol = 0x86dd;
		else return 0;
		offset = 22;
	}
	if (protocol == 0x0800) tos = packet[offset + 1];
	else if (protocol == 0x86dd) tos = (packet[offset] << 4 | packet[offset + 1] >> 4) & 255;
	else return 0;
	return tid[tos >> 5];
}
#endif
