/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_XGPON_HW_H
#define RTL9617C_XGPON_HW_H

#include "rtl9617c-qm-deepq.h"

#define RTL_XGPON_PLOAM_BYTES 48
#define RTL_XGPON_PLOAM_TX_BYTES 40
#define RTL_XGPON_ALLOC_ID_INVALID 0xbaadU /* Original ASIC invalid Alloc-ID. */

static inline void rtl_xgpon_key_words(const struct rtl_dq_io *io, unsigned int address,
		const unsigned char key[16])
{
	unsigned int i;

	for (i = 0; i < 16; i += 4)
		io->write(io->context, address + i, (unsigned int)key[i] << 24 |
			(unsigned int)key[i + 1] << 16 | (unsigned int)key[i + 2] << 8 | key[i + 3]);
}

static inline void rtl_xgpon_data_key(const struct rtl_dq_io *io, unsigned int index,
		const unsigned char key[16])
{
	/* Stock aal_xgpon_aes_uc_enc_key_set with p_key_active=0 and length16. */
	io->write(io->context, 0x4614, io->read(io->context, 0x4614));
	io->write(io->context, 0x4e00, (io->read(io->context, 0x4e00) & ~31U) | 16);
	rtl_xgpon_key_words(io, 0x4f00 + index * 16, key);
}

static inline void rtl_xgpon_key_confirm(const struct rtl_dq_io *io, unsigned int index)
{
	unsigned int value = io->read(io->context, 0x4614);

	if ((value & 1) != index) io->write(io->context, 0x4614, (value & ~1U) | index);
	io->write(io->context, 0x4600, io->read(io->context, 0x4600) | (1U << 7));
}

static inline void rtl_xgpon_multicast_key(const struct rtl_dq_io *io, unsigned int index,
		const unsigned char key[16], int enabled)
{
	unsigned int valid = io->read(io->context, 0x4128);
	unsigned int control = io->read(io->context, 0x4e00);

	rtl_xgpon_key_words(io, 0x4f20 + index * 16, key);
	io->write(io->context, 0x4128, (valid & ~(1U << index)) | (enabled ? 1U << index : 0));
	io->write(io->context, 0x4e00, (control & ~31U) | 16);
}

static inline void rtl_xgpon_key_report(unsigned char message[40], unsigned int onu_id,
		unsigned int control, const unsigned char fragment[16])
{
	unsigned int i;

	for (i = 0; i < 40; i++) message[i] = 0;
	message[0] = onu_id >> 8; message[1] = onu_id;
	message[2] = 5; message[3] = control;
	message[4] = control >> 8; message[5] = control >> 16;
	for (i = 0; i < 16; i++) message[i + 8] = fragment[i];
}

static inline void rtl_xgpon_active(const struct rtl_dq_io *io, int enabled, int symmetric)
{
	/* Stock aal_pon_mac_enable_set: GPON family mode minus GPON is 1 or 2. */
	io->write(io->context, 0x4004, (io->read(io->context, 0x4004) & ~3U) |
		(enabled ? symmetric ? 2U : 1U : 0));
}

static inline unsigned int rtl_xgpon_be32(const unsigned char *data)
{
	return (unsigned int)data[0] << 24 | (unsigned int)data[1] << 16 |
	       (unsigned int)data[2] << 8 | data[3];
}

/* Original xgpon_aes_ploam_ack_mic_update and aal_ploam_ack_ploam_no_msg_set. */
static inline int rtl_xgpon_ack_no_message(const struct rtl_dq_io *io, void *context,
	int (*cmac)(void *, const unsigned char *, const unsigned char *, unsigned int, unsigned char *),
	const unsigned char key[16], unsigned int onu_id)
{
	unsigned char message[41] = {2}, mic[16];
	int ret;

	message[1] = onu_id >> 8;
	message[2] = onu_id;
	message[3] = 9;
	message[5] = 1;
	ret = cmac(context, key, message, sizeof(message), mic);
	if (!ret) {
		io->write(io->context, 0x4a64, rtl_xgpon_be32(mic + 4));
		io->write(io->context, 0x4a68, rtl_xgpon_be32(mic));
	}
	return ret;
}

static inline void rtl_xgpon_identity(const struct rtl_dq_io *io,
				     const unsigned char serial[8],
				     const unsigned char registration[36])
{
	unsigned int fragment;

	io->write(io->context, 0x4a10, rtl_xgpon_be32(serial));
	io->write(io->context, 0x4a14, rtl_xgpon_be32(serial + 4));
	for (fragment = 0; fragment < 9; fragment++)
		io->write(io->context, 0x4a54 - fragment * 4,
			  rtl_xgpon_be32(registration + fragment * 4));
}

static inline void rtl_xgpon_mac_parameters(const struct rtl_dq_io *io, int symmetric)
{
	unsigned int value;

	/* MAC remains disabled; XG-PON uses2.488G upstream, XGS-PON9.953G. */
	io->write(io->context, 0x4004,
		  (io->read(io->context, 0x4004) & ~0xf3U) | (symmetric ? 0xa0 : 0x60));
	io->write(io->context, 0x4108, io->read(io->context, 0x4108) | 0x1000000fU);
	value = io->read(io->context, 0x4110);
	/* Rev-C keeps bit4 of the OMCI minimum size, as the original setter does. */
	io->write(io->context, 0x4110,
		  (value & ~0x7ffe03e0U) | (12000U << 17) | (14U << 4));
	io->write(io->context, 0x4e00, 0x50); /* Hardware PLOAM key derivation,16-byte keys. */
	io->write(io->context, 0x4e04, 0);
	io->write(io->context, 0x4a04,
		  (io->read(io->context, 0x4a04) & ~0x01800011U) | 0x01800010U);
	io->write(io->context, 0x4a00, 0x7e);
	io->write(io->context, 0x4a08, ~0U);
	io->write(io->context, 0x4a60, 0xffffff);
	/* The stock scheduler uses different burst margins and ahead times. */
	io->write(io->context, 0x4808, symmetric ? 0x40 : 0x27);
	io->write(io->context, 0x4804, 0x1543);
	io->write(io->context, 0x480c, symmetric ? 0x200 : 0x100);
	io->write(io->context, 0x4d00,
		  (io->read(io->context, 0x4d00) & ~0xff1U) | 0xa0);
	io->write(io->context, 0x4d08, 80000);
	io->write(io->context, 0x4d0c, 80000);
	io->write(io->context, 0x4d10, 1000);
	io->write(io->context, 0x4d14, 1000);
	io->write(io->context, 0x4d18, 1000);
	io->write(io->context, 0x4d04, 80008);
	io->write(io->context, 0x4d30, io->read(io->context, 0x4d30) & ~0x77U);
	io->write(io->context, 0x4d2c, 0x07a3dfff);
	io->write(io->context, 0x4d28, 0x0fffffff);
	io->write(io->context, 0x4600,
		  (io->read(io->context, 0x4600) & ~0xff0000c0U) | 0x04000040U);
	/* Stock PLOAMU queue config selects no_empty bit0 and mode bit31. */
	io->write(io->context, 0x4628, io->read(io->context, 0x4628) & ~0x80000001U);
	io->write(io->context, 0x410c, io->read(io->context, 0x410c) | 6U);
}

/* XGTC Alloc-ID is 14 bits; this table is indexed by T-CONT, not Alloc-ID. */
static inline int rtl_xgpon_tcont_set(const struct rtl_dq_io *io,
				     unsigned int index, unsigned int alloc_id,
				     int ploam, int omci)
{
	unsigned int word;
	int ret = io->indirect(io->context, 0x4838, 0x80000000U | index);

	if (ret)
		return ret;
	word = (io->read(io->context, 0x483c) & ~0x11003fffU) |
	       (alloc_id & 0x3fffU) | (ploam ? 1U << 28 : 0) | (omci ? 1U << 24 : 0);
	io->write(io->context, 0x483c, word);
	return io->indirect(io->context, 0x4838, 0xc0000000U | index);
}

/* Stock OMCI deletion leaves a PLOAM-enabled allocation under line control. */
static inline int rtl_xgpon_tcont_release(const struct rtl_dq_io *io, unsigned int index)
{
	unsigned int word;
	int ret = io->indirect(io->context, 0x4838, 0x80000000U | index);

	if (ret)
		return ret;
	word = io->read(io->context, 0x483c);
	if (word & (1U << 28))
		return 0;
	io->write(io->context, 0x483c, (word & ~0x01003fffU) |
		(RTL_XGPON_ALLOC_ID_INVALID & 0x3fffU));
	return io->indirect(io->context, 0x4838, 0xc0000000U | index);
}

/* Both XG-PON and XGS-PON retain the complete 16-bit XGEM identifier. */
static inline void rtl_xgpon_downstream_gem_set(const struct rtl_dq_io *io,
					      unsigned int index, unsigned int gem_id,
					      int valid, int multicast)
{
	unsigned int address = 0x412c + index * 4;
	unsigned int word = (io->read(io->context, address) & ~0x3ffffU) |
		gem_id | (multicast ? 1U << 16 : 0) | (valid ? 1U << 17 : 0);

	io->write(io->context, address, word);
}

/* The protocol owner updates the global AES state when the last key user exits. */
static inline int rtl_xgpon_upstream_gem_set(const struct rtl_dq_io *io,
					   unsigned int index, unsigned int gem_id,
					   int enabled, int encrypted, int multicast)
{
	unsigned int word;
	int ret = io->indirect(io->context, 0x4618, 0x80000000U | index);

	if (ret)
		return ret;
	word = (io->read(io->context, 0x461c) & ~0x7ffffU) | gem_id |
	       (enabled ? 1U << 16 : 0) | (encrypted ? 1U << 17 : 0) |
	       (multicast ? 1U << 18 : 0);
	io->write(io->context, 0x461c, word);
	return io->indirect(io->context, 0x4618, 0xc0000000U | index);
}

/* DSFP OMCC-AES change mirrors the transmit mode into all eight OMCC GEMs.
 * Read the live mode so a MAC reset cannot leave a separate toggle stale. */
static inline int rtl_xgpon_omcc_aes_toggle(const struct rtl_dq_io *io)
{
	unsigned int encrypted, word;
	int ret = io->indirect(io->context, 0x4618, 0x80000000U);

	if (ret)
		return ret;
	encrypted = (io->read(io->context, 0x461c) ^ (1U << 17)) & (1U << 17);
	for (unsigned int i = 0; i < 8; i++) {
		if (i) {
			ret = io->indirect(io->context, 0x4618, 0x80000000U | i);
			if (ret)
				return ret;
		}
		word = (io->read(io->context, 0x461c) & ~(1U << 17)) | encrypted;
		io->write(io->context, 0x461c, word);
		ret = io->indirect(io->context, 0x4618, 0xc0000000U | i);
		if (ret)
			return ret;
	}
	if (!encrypted) {
		/* Original setter releases AES only after its last enabled GEM exits. */
		for (unsigned int i = 8; i < 256; i++) {
			ret = io->indirect(io->context, 0x4618, 0x80000000U | i);
			if (ret)
				return ret;
			if ((io->read(io->context, 0x461c) & (3U << 16)) == (3U << 16))
				return 0;
		}
		io->write(io->context, 0x4600, io->read(io->context, 0x4600) & ~(1U << 7));
	}
	return 0;
}

/* TURN selects the older ready buffer; silicon authenticates before PKT_RDY. */
static inline unsigned int rtl_xgpon_ploam_order(unsigned int status0,
					       unsigned int status1,
					       unsigned char order[2])
{
	unsigned int count = 0;

	if (status0 & 0x80000000U)
		order[count++] = 0;
	if (status1 & 0x80000000U)
		order[count++] = 1;
	if (count == 2 && !(status0 & 0x40000000U) && (status1 & 0x40000000U)) {
		order[0] = 1;
		order[1] = 0;
	}
	return count;
}

static inline void rtl_xgpon_ploam_receive(const struct rtl_dq_io *io,
					 unsigned int buffer,
					 unsigned char message[RTL_XGPON_PLOAM_BYTES])
{
	unsigned int i, word;

	for (i = 0; i < RTL_XGPON_PLOAM_BYTES; i += 4) {
		word = io->read(io->context, 0x4b80 + buffer * 48 + i);
		message[i] = word >> 24;
		message[i + 1] = word >> 16;
		message[i + 2] = word >> 8;
		message[i + 3] = word;
	}
	/* Return ownership only after copying all content and MIC. */
	io->write(io->context, 0x4b28 + buffer * 4, 0);
}

/* Caller waits for PKT_RDY=0; hardware appends the eight-byte PLOAM MIC. */
static inline void rtl_xgpon_ploam_transmit(const struct rtl_dq_io *io,
					  const unsigned char message[RTL_XGPON_PLOAM_TX_BYTES],
					  int default_key)
{
	unsigned int i, word;

	for (i = 0; i < RTL_XGPON_PLOAM_TX_BYTES; i += 4) {
		word = (unsigned int)message[i] << 24 | (unsigned int)message[i + 1] << 16 |
		       (unsigned int)message[i + 2] << 8 | message[i + 3];
		io->write(io->context, 0x4b00 + i, word);
	}
	word = (io->read(io->context, 0x4afc) & ~1U) | (default_key ? 1U : 0);
	io->write(io->context, 0x4afc, word | 0x80000000U);
}
#endif
