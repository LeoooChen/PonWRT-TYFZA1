/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_OMCI_FORMAT_H
#define RTL9617C_PON_OMCI_FORMAT_H

#define RTL_OMCI_PREFIX_BYTES 16
#define RTL_OMCI_MAX_BODY 1980

/* Original ca_generate_msk -> ca_generate_sk, with Class332 MSK replacement. */
static inline int rtl_omci_session_key(void *context,
	int (*cmac)(void *, const unsigned char *, const unsigned char *, unsigned int, unsigned char *),
	const unsigned char serial[8], const unsigned char registration[36],
	const unsigned char tag[8], const unsigned char *supplied_msk, unsigned char result[16])
{
	unsigned char root[16], msk[16], message[24];
	unsigned int i;
	int ret = 0;

	for (i = 0; i < 16; i++) root[i] = 0x55;
	if (supplied_msk)
		for (i = 0; i < 16; i++) msk[i] = supplied_msk[i];
	else
		ret = cmac(context, root, registration, 36, msk);
	if (ret) return ret;
	for (i = 0; i < 8; i++) { message[i] = serial[i]; message[i + 8] = tag[i]; }
	for (i = 0; i < 8; i++) message[i + 16] = "SessionK"[i];
	ret = cmac(context, msk, message, 24, result);
	for (i = 0; i < 16; i++) msk[i] = 0;
	return ret;
}

static inline int rtl_omci_derive_key(void *context,
	int (*cmac)(void *, const unsigned char *, const unsigned char *, unsigned int, unsigned char *),
	const unsigned char serial[8], const unsigned char registration[36],
	const unsigned char tag[8], unsigned char result[16])
{
	unsigned char session[16];
	static const unsigned char label[16] = "OMCIIntegrityKey";
	unsigned int i;
	int ret = rtl_omci_session_key(context, cmac, serial, registration, tag, 0, session);

	if (!ret) ret = cmac(context, session, label, 16, result);
	for (i = 0; i < 16; i++) session[i] = 0;
	return ret;
}

/* Userspace supplies the OMCI body; the transport appends CRC/MIC. */
static inline unsigned int rtl_omci_body_length(const unsigned char *data,
					       unsigned int length)
{
	if (length < 8)
		return 0;
	if (data[3] == 0x0a)
		return 44;
	if (data[3] == 0x0b && length >= 10)
		return 10 + ((unsigned int)data[8] << 8 | data[9]);
	return 0;
}

static inline unsigned int rtl_omci_gpon_crc(const unsigned char *data,
					    unsigned int length)
{
	unsigned int crc = ~0U, byte, bit;

	for (byte = 0; byte < length; byte++) {
		crc ^= (unsigned int)data[byte] << 24;
		for (bit = 0; bit < 8; bit++)
			crc = (crc << 1) ^ (crc & 0x80000000U ? 0x04c11db7U : 0);
	}
	return ~crc;
}

static inline void rtl_omci_prefix(unsigned char prefix[RTL_OMCI_PREFIX_BYTES],
				    unsigned int wire_length, unsigned char priority)
{
	static const unsigned char header[RTL_OMCI_PREFIX_BYTES] = {
		0x00, 0x13, 0x25, 0x00, 0x00, 0x00,
		0x00, 0x13, 0x25, 0x00, 0x00, 0x01,
		0xff, 0xf1, 0x00, 0x00,
	};
	unsigned int byte;

	for (byte = 0; byte < RTL_OMCI_PREFIX_BYTES; byte++)
		prefix[byte] = header[byte];
	prefix[14] = wire_length < 48 ? wire_length : 0;
	prefix[15] = priority >> 7;
}

static inline unsigned int rtl_omci_receive_offset(const unsigned char *data,
						  unsigned int length)
{
	if (length >= 16 && data[12] == 0xff && data[13] == 0xf1)
		return 16;
	if (length >= 20 && data[12] == 0x81 && data[13] == 0 &&
	    data[16] == 0xff && data[17] == 0xf1)
		return 20;
	return 0;
}
#endif
