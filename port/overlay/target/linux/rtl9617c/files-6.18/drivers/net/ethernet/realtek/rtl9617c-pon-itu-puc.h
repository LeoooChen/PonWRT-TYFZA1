/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_ITU_PUC_H
#define RTL9617C_PON_ITU_PUC_H

#include "rtl9617c-pon-queue.h"

static inline int rtl_itu_puc_tcont_weights(const struct rtl_dq_io *io,
		unsigned int index, unsigned int enabled, const unsigned char weights[8])
{
	unsigned int data[5] = {0}, cos;
	unsigned long long field;

	for (cos = 0; cos < 8; cos++) {
		unsigned int bit = cos * 9;

		field = (index * 8 + cos) | ((enabled >> cos & 1U) << 8);
		data[bit / 32] |= field << (bit % 32);
		if (bit % 32 > 23)
			data[bit / 32 + 1] |= field >> (32 - bit % 32);
	}
	if (weights) {
		data[2] |= (unsigned int)weights[0] << 16 | (unsigned int)weights[1] << 24;
		for (cos = 2; cos < 6; cos++)
			data[3] |= (unsigned int)weights[cos] << ((cos - 2) * 8);
		data[4] = weights[6] | (unsigned int)weights[7] << 8;
	}
	/* A valid T-CONT entry can contain disabled business queues. */
	data[2] |= 1U << 12;
	for (cos = 0; cos < 5; cos++)
		io->write(io->context, 0x8014 - cos * 4, data[cos]);
	return io->indirect(io->context, 0x8000, 0xc0000000U | index);
}

static inline int rtl_itu_puc_tcont(const struct rtl_dq_io *io,
				  unsigned int index, unsigned int enabled)
{
	return rtl_itu_puc_tcont_weights(io, index, enabled, 0);
}

/* TYFZA1 ponmac_queue_add updates both the PVT weight and its VSTBL credit. */
static inline int rtl_itu_puc_schedule(const struct rtl_dq_io *io,
		unsigned int index, unsigned int enabled, const unsigned char weights[8])
{
	unsigned int data[5] = {0}, cos, shift = 7 + (io->read(io->context, 0x8048) & 3);
	int ret = rtl_itu_puc_tcont_weights(io, index, enabled, weights);

	if (ret)
		return ret;
	for (cos = 0; cos < 8; cos++) {
		unsigned int bit = cos * 20;
		unsigned long long credit = (unsigned int)weights[cos] << shift;

		data[bit / 32] |= credit << (bit % 32);
		if (bit % 32 > 12)
			data[bit / 32 + 1] |= credit >> (32 - bit % 32);
	}
	for (cos = 0; cos < 5; cos++)
		io->write(io->context, 0x8074 - cos * 4, data[cos]);
	return io->indirect(io->context, 0x8060, 0xc0000000U | index);
}

static inline void rtl_gpon_puc_profile(const struct rtl_dq_io *io)
{
	unsigned int mask = 0xfb01193fU;

	/* GEM fragmentation, five-byte header, no word alignment. */
	io->write(io->context, 0x8050,
		  (io->read(io->context, 0x8050) & ~mask) | 0x2b010005U);
	io->write(io->context, 0x8230, 0x00c80000);
	io->write(io->context, 0x8234, 0x05c201b8);
}

static inline void rtl_xgpon_puc_profile(const struct rtl_dq_io *io)
{
	unsigned int mask = 0xfb01193fU;

	/* XGEM fragmentation, eight-byte header and word alignment. */
	io->write(io->context, 0x8050,
		  (io->read(io->context, 0x8050) & ~mask) | 0x7b001008U);
	io->write(io->context, 0x8230, 0x00800000);
	io->write(io->context, 0x8234, 0x10000200);
}

static inline int rtl_itu_puc_queues(const struct rtl_dq_io *io)
{
	unsigned int index;
	int ret;

	io->write(io->context, 0x804c, 0);
	for (index = 0; index < 32; index++) {
		/* Only T-CONT0 serves OMCC before OMCI assigns business queues. */
		ret = rtl_itu_puc_tcont(io, index, index ? 0 : 255);
		if (ret)
			return ret;
	}
	for (index = 0; index < 8; index++)
		io->write(io->context, 0x81bc - index * 4, index ? 0 : 255);
	io->write(io->context, 0x8160,
		  (io->read(io->context, 0x8160) & ~0xc00007ffU) | 0x80000606U);
	io->write(io->context, 0x8164,
		  (io->read(io->context, 0x8164) & ~0x7ffU) | 0x707U);
	io->write(io->context, 0x8168,
		  (io->read(io->context, 0x8168) & ~0x803f0000U) | 0x801e0000U);
	return rtl_pon_queue_backpressure(io);
}
#endif
