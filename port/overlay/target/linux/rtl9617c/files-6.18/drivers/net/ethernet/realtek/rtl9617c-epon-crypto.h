/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_EPON_CRYPTO_H
#define RTL9617C_EPON_CRYPTO_H
#include <linux/errno.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-qm-deepq.h"

static inline void rtl_epon_crypto_update(const struct rtl_dq_io *io,
	unsigned int reg, unsigned int mask, unsigned int value)
{
	io->write(io->context, reg, (io->read(io->context, reg) & ~mask) | value);
}

/* Actual aal_epon_crypto_init defaults: 1G churning, XG triple churning. */
static inline void rtl_epon_crypto_profile(const struct rtl_dq_io *io, enum rtl_pon_mode mode)
{
	if (mode == RTL_PON_EPON_1G) {
		rtl_epon_crypto_update(io, 0x8b4, 7U << 29, 0);
		rtl_epon_crypto_update(io, 0x93c, 1U | (1U << 7), 1);
		rtl_epon_crypto_update(io, 0x8a8, 4, 0);
		rtl_epon_crypto_update(io, 0x940, 8, 8);
	} else {
		rtl_epon_crypto_update(io, 0x20, 15, 10);
		rtl_epon_crypto_update(io, 0x1250, 1U << 28, 0);
		rtl_epon_crypto_update(io, 0x1600, 2, 0);
		rtl_epon_crypto_update(io, 0x14c0, 2, 0);
	}
}

static inline int rtl_epon_crypto_llid(const struct rtl_dq_io *io, enum rtl_pon_mode mode,
	unsigned int channel, unsigned int llid, int enable)
{
	unsigned int value, direction;
	int ret;

	if (mode == RTL_PON_EPON_1G) {
		/* Valid is bit19. A disabled entry must not retain a key index that
		 * overlaps that bit, as the original setter does for channels4..7. */
		value = enable ? llid | (((channel * 2) & 7) << 16) | (1U << 19) : 0;
		io->write(io->context, 0x95c + channel * 4, value);
		return 0;
	}
	for (direction = 0; direction < 2; direction++) {
		unsigned int access = direction ? 0x14dc : 0x161c;
		unsigned int data = direction ? 0x14e0 : 0x1620;

		ret = io->indirect(io->context, access, (1U << 31) | channel);
		if (ret)
			return ret;
		value = io->read(io->context, data) & ~0x7fffffU;
		value |= llid | (channel << 17) | (direction && enable ? 1U << 16 : 0);
		io->write(io->context, data, value);
		ret = io->indirect(io->context, access, (3U << 30) | channel);
		if (ret)
			return ret;
	}
	return 0;
}

/* Key packing established by executing the board's actual DAL for both slots.
 * SA reads preserve all other keys and fields; a write completes before ACK. */
static inline int rtl_epon_crypto_key(const struct rtl_dq_io *io, enum rtl_pon_mode mode,
	unsigned int channel, unsigned int slot, const unsigned char *key)
{
	unsigned char packed[16] = {};
	unsigned int base, direction, word, byte, value;
	int ret;

	if (mode == RTL_PON_EPON_1G) {
		base = 0x8b8 + slot * 16;
		io->write(io->context, base, (unsigned int)key[0] << 24 |
			(unsigned int)key[1] << 16 | (unsigned int)key[2] << 8);
		for (word = 1; word < 4; word++)
			io->write(io->context, base + word * 4, 0);
		return 0;
	}
	if (!(io->read(io->context, 0x1434) & 1))
		return -EAGAIN;
	/* aal_epon_crypto_xg_enable_set(TX,0) also removes MACsec overhead
	 * from payload and REPORT accounting, not just the TX LLID valid bit. */
	rtl_epon_crypto_update(io, 0x1010, 1U << channel, 0);
	rtl_epon_crypto_update(io, 0x8054, 0x3f0, 0x80);
	rtl_epon_crypto_update(io, 0x8050, 0x3f, 0x14);
	for (byte = 0; byte < 9; byte++)
		packed[byte] = key[(byte / 3) * 3 + 2 - byte % 3];
	for (direction = 0; direction < 2; direction++) {
		/* Stock DAL updates TX first, then RX; upstream encryption stays off. */
		unsigned int access = direction ? 0x1438 : 0x1578;
		unsigned int control = direction ? 0x1488 : 0x15c8;

		ret = io->indirect(io->context, access, (channel << 2) | 1);
		if (ret)
			return ret;
		base = (direction ? 0x1448 : 0x1588) + slot * 32;
		for (word = 0; word < 4; word++) {
			value = 0;
			for (byte = 0; byte < 4; byte++)
				value |= (unsigned int)packed[word * 4 + byte] << (byte * 8);
			io->write(io->context, base - word * 4, value);
		}
		rtl_epon_crypto_update(io, control, 0, 1U << (slot * 2));
		ret = io->indirect(io->context, access, (channel << 2) | 3);
		if (ret)
			return ret;
	}
	/* Original DAL: gate threshold100, deregistration disabled after exchange. */
	rtl_epon_crypto_update(io, 0x204c, 0x3fffU << 16, 100U << 16);
	rtl_epon_crypto_update(io, 0x2024, 1U << 17, 0);
	return 0;
}
#endif
