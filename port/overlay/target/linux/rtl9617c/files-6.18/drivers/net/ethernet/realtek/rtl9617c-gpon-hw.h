/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_GPON_HW_H
#define RTL9617C_GPON_HW_H

#include <linux/errno.h>
#include "rtl9617c-qm-deepq.h"

#define RTL_GPON_PLOAM_BYTES 13

static inline unsigned int rtl_gpon_be32(const unsigned char *bytes)
{
	return (unsigned int)bytes[0] << 24 | (unsigned int)bytes[1] << 16 |
	       (unsigned int)bytes[2] << 8 | bytes[3];
}

static inline void rtl_gpon_identity(const struct rtl_dq_io *io,
				   const unsigned char serial[8],
				   const unsigned char password[10])
{
	io->write(io->context, 0x6014, rtl_gpon_be32(serial));
	io->write(io->context, 0x6018, rtl_gpon_be32(serial + 4));
	io->write(io->context, 0x601c, (unsigned int)password[0] << 8 | password[1]);
	io->write(io->context, 0x6020, rtl_gpon_be32(password + 2));
	io->write(io->context, 0x6024, rtl_gpon_be32(password + 6));
}

static inline void rtl_gpon_mac_parameters(const struct rtl_dq_io *io)
{
	io->write(io->context, 0x6118, 0x12100900);
	io->write(io->context, 0x6000,
		  (io->read(io->context, 0x6000) & ~0x3fff0000U) | 0x3fff0000U);
}

static inline void rtl_gpon_active(const struct rtl_dq_io *io, int enabled)
{
	io->write(io->context, 0x6114,
		  (io->read(io->context, 0x6114) & ~2U) | (enabled ? 2U : 0));
}

static inline void rtl_gpon_next_key(const struct rtl_dq_io *io,
				     const unsigned char key[16])
{
	unsigned int pointer = io->read(io->context, 0x6078);
	unsigned int bank = pointer & (1U << 24) ? 0 : 1;
	unsigned int indices = io->read(io->context, 0x602c);
	unsigned int shift = bank ? 24 : 16;
	unsigned int index = (indices >> shift) & 255U;
	unsigned int fragment;

	for (fragment = 0; fragment < 4; fragment++) {
		const unsigned char *bytes = key + 12 - fragment * 4;

		io->write(io->context, 0x6030 + bank * 16 + fragment * 4,
			  bytes[0] | (unsigned int)bytes[1] << 8 |
			  (unsigned int)bytes[2] << 16 | (unsigned int)bytes[3] << 24);
	}
	/* The original bank0 sequence wraps before255; bank1 includes255. */
	index = bank ? (index + 1) & 255U : index >= 254 ? 0 : index + 1;
	io->write(io->context, 0x602c,
		  (indices & ~(255U << shift)) | index << shift);
	io->write(io->context, 0x6078, pointer & ~(1U << 25));
}

/* FIFO3's upper byte is the remaining-entry count, followed by 13 wire bytes. */
static inline unsigned int rtl_gpon_ploam_receive(const struct rtl_dq_io *io,
						 unsigned char message[RTL_GPON_PLOAM_BYTES])
{
	unsigned int words[4], i, value;

	io->write(io->context, 0x60f4, 1);
	for (i = 0; i < 4; i++)
		words[i] = io->read(io->context, 0x6104 - i * 4);
	message[0] = words[3] & 255;
	for (i = 0; i < 12; i++) {
		value = words[2 - i / 4];
		message[1 + i] = value >> (24 - (i % 4) * 8);
	}
	return (words[3] >> 8) & 127;
}

/* Caller waits for req=0 before filling; silicon inserts ONU-ID and CRC. */
static inline void rtl_gpon_ploam_transmit(const struct rtl_dq_io *io,
					  unsigned char type,
					  const unsigned char payload[10])
{
	unsigned int word;

	io->write(io->context, 0x60ec, rtl_gpon_be32(payload + 6));
	io->write(io->context, 0x60e8, rtl_gpon_be32(payload + 2));
	word = io->read(io->context, 0x60e4) & 0xff00U;
	word |= type | ((unsigned int)payload[0] << 24) | ((unsigned int)payload[1] << 16);
	io->write(io->context, 0x60e4, word);
	io->write(io->context, 0x60e0, io->read(io->context, 0x60e0) | 1U);
}

static inline int rtl_gpon_tcont_set(const struct rtl_dq_io *io,
				    unsigned int alloc_id, unsigned int index,
				    int ploam, int omci, int software_ploam)
{
	unsigned int command = 0x80000000U | (software_ploam ? 1U << 16 : 0) | alloc_id;
	unsigned int word;
	int ret = io->indirect(io->context, 0x612c, command);

	if (ret)
		return ret;
	word = (io->read(io->context, 0x6130) & ~127U) |
	       index << 2 | (omci ? 2U : 0) | (ploam ? 1U : 0);
	io->write(io->context, 0x6130, word);
	ret = io->indirect(io->context, 0x612c, command | 0x40000000U);
	if (ret)
		return ret;
	/* The board's original setter reads back before publishing its T-CONT. */
	ret = io->indirect(io->context, 0x612c, command);
	if (ret)
		return ret;
	return (io->read(io->context, 0x6130) & 127U) == (word & 127U) ? 0 : -EIO;
}

static inline int rtl_gpon_downstream_gem_set(const struct rtl_dq_io *io,
					    unsigned int gem_id, unsigned int index,
					    int valid, int encrypted, int software_aes)
{
	unsigned int command = 0x80000000U | (software_aes ? 1U << 16 : 0) | gem_id;
	unsigned int word;
	int ret = io->indirect(io->context, 0x6134, command);

	if (ret)
		return ret;
	word = (io->read(io->context, 0x6138) & ~0x7fbU) |
	       index << 3 | (encrypted ? 2U : 0) | (valid ? 1U : 0);
	io->write(io->context, 0x6138, word);
	ret = io->indirect(io->context, 0x6134, command | 0x40000000U);
	if (ret)
		return ret;
	ret = io->indirect(io->context, 0x6134, command);
	if (ret)
		return ret;
	return (io->read(io->context, 0x6138) & 0x7fbU) == (word & 0x7fbU) ? 0 : -EIO;
}

static inline int rtl_gpon_upstream_gem_set(const struct rtl_dq_io *io,
					  unsigned int index, unsigned int gem_id)
{
	int ret = io->indirect(io->context, 0x6170, 0x80000000U | index);

	if (ret)
		return ret;
	io->write(io->context, 0x6174, (io->read(io->context, 0x6174) & ~4095U) | gem_id);
	return io->indirect(io->context, 0x6170, 0xc0000000U | index);
}
#endif
