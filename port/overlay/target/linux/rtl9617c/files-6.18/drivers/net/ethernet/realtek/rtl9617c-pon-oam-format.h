/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_OAM_FORMAT_H
#define RTL9617C_PON_OAM_FORMAT_H

#define RTL_OAM_PREFIX_BYTES 16

/* RTL8277C EPON DAL: PUC strips this internal Ethernet container. */
static inline void rtl_oam_prefix(unsigned char *header, unsigned int llid)
{
	static const unsigned char value[RTL_OAM_PREFIX_BYTES] = {
		0x00, 0x13, 0x25, 0x00, 0x00, 0x00,
		0x00, 0x13, 0x25, 0x00, 0x00, 0x01,
		0xff, 0xf1, 0x00, 0x00,
	};
	unsigned int i;

	for (i = 0; i < RTL_OAM_PREFIX_BYTES; i++)
		header[i] = value[i];
	header[5] = llid & 0x3f;
}

static inline int rtl_oam_is_frame(const unsigned char *data, unsigned int length)
{
	return length >= 15 && data[12] == 0x88 && data[13] == 0x09 && data[14] == 0x03;
}

static inline unsigned int rtl_oam_receive_offset(const unsigned char *data,
						unsigned int length, unsigned int *llid)
{
	static const unsigned char destination[6] = {0x00, 0x13, 0x25, 0x00, 0x00, 0x01};
	unsigned int i, offset;

	if (length < RTL_OAM_PREFIX_BYTES + 15)
		return 0;
	for (i = 0; i < 6; i++)
		if (data[i] != destination[i])
			return 0;
	if (data[12] == 0xff && data[13] == 0xf1)
		offset = 16;
	else if (data[12] == 0x81 && data[13] == 0x00 && length >= 35 &&
		 data[16] == 0xff && data[17] == 0xf1)
		offset = 20;
	else
		return 0;
	if (!rtl_oam_is_frame(data + offset, length - offset))
		return 0;
	*llid = data[11] & 0x3f;
	return offset;
}

#endif
