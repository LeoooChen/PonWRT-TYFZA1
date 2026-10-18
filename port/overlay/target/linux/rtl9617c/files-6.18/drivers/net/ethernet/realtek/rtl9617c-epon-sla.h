/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_EPON_SLA_H
#define RTL9617C_EPON_SLA_H

#include "rtl9617c-pon-itu-puc.h"

#define RTL_EPON_SLA_BYTES 88

static inline unsigned int rtl_epon_sla_u16(const unsigned char *data)
{
	return (unsigned int)data[0] << 8 | data[1];
}

static inline int rtl_epon_sla_validate(const unsigned char *data, unsigned int size)
{
	unsigned int row;

	if (size == 1)
		return data[0] ? -EINVAL : 0;
	if (size < 18 || data[0] != 1 || data[1] > 1 ||
	    !data[7] || data[7] > 8 || size != 8U + 10U * data[7])
		return -EINVAL;
	for (row = 0; row < data[7]; row++) {
		const unsigned char *queue = data + 8 + row * 10;

		if (rtl_epon_sla_u16(queue + 5) > rtl_epon_sla_u16(queue + 7) ||
		    queue[9] > 100 || (data[1] == 1 && !queue[9]))
			return -EINVAL;
	}
	return 0;
}

/* rt_epon_ponQueue_set scales the wire rate by eight; the actual DAL splits
 * it into /500 and %500 and selects these four burst sizes. Preserve the
 * hardware bucket mode rather than replacing it with a guessed constant. */
static inline int rtl_epon_sla_bucket(const struct rtl_dq_io *io,
	unsigned int queue, unsigned int rate, int pir)
{
	unsigned int access = pir ? 0x81ec : 0x81c4;
	unsigned int data0 = access + 8, data1 = access + 4;
	unsigned int burst, low, high;
	int ret = io->indirect(io->context, access, 0x80000000U | queue);

	if (ret)
		return ret;
	high = io->read(io->context, data1) & 0x800000c0U;
	if (rate == 0x1f3ffffU) {
		low = pir ? ~0U : 0;
		if (pir)
			high |= 0x3f;
	} else {
		burst = rate < 25000 ? 5 : rate < 50000 ? 10 : rate < 500000 ? 100 : 1000;
		low = rate % 500 | (rate / 500) << 10 | (burst & 63) << 26;
		high |= burst >> 6;
	}
	io->write(io->context, data0, low);
	io->write(io->context, data1, high);
	return io->indirect(io->context, access, 0xc0000000U | queue);
}

static inline int rtl_epon_sla_flush(const struct rtl_dq_io *io, unsigned int port,
	unsigned int queue)
{
	unsigned int limit_reg = 0x8090 + queue / 16 * 4;
	unsigned int limit, drop, command;
	int ret = io->indirect(io->context, 0x802c, 0x80000000U | queue);

	if (ret)
		return ret;
	if (!io->read(io->context, 0x8034) && !(io->read(io->context, 0x8030) & 7))
		return 0;
	limit = io->read(io->context, limit_reg);
	drop = io->read(io->context, 0x80e0);
	io->write(io->context, 0x80e0, (drop & ~0x800000ffU) | 0x80000000U | queue);
	io->write(io->context, limit_reg, limit & ~(3U << ((queue % 16) * 2)));
	command = (io->read(io->context, 0x80dc) & ~0x80011fffU) |
		0x80000000U | port << 8 | queue;
	ret = io->indirect(io->context, 0x80dc, command);
	io->write(io->context, limit_reg, limit);
	io->write(io->context, 0x80e0, drop);
	if (ret)
		return ret;
	ret = io->indirect(io->context, 0x802c, 0x80000000U | queue);
	if (ret)
		return ret;
	return io->read(io->context, 0x8034) || (io->read(io->context, 0x8030) & 7) ? -EIO : 0;
}

static inline int rtl_epon_sla_apply(const struct rtl_dq_io *io, unsigned int port,
	const unsigned char *data)
{
	unsigned char weights[8] = {0};
	unsigned int queues = data[0] ? data[7] : 8;
	unsigned int valid_reg = 0x81bc - port / 4 * 4;
	unsigned int shift = (port % 4) * 8, row;
	int ret;

	/* Stop this LLID's queues while replacing the policy. A failed table
	 * operation must not leave a partially applied policy advertised live. */
	ret = rtl_itu_puc_tcont_weights(io, port, 0, weights);
	if (ret)
		return ret;
	io->write(io->context, valid_reg, io->read(io->context, valid_reg) & ~(255U << shift));
	for (row = 0; row < 8; row++) {
		unsigned int cir = 0, pir = 0x1f3ffffU;

		ret = rtl_epon_sla_flush(io, port, port * 8 + row);
		if (ret)
			return ret;
		if (data[0] && row < queues) {
			const unsigned char *queue = data + 8 + row * 10;

			cir = rtl_epon_sla_u16(queue + 5) * 8;
			pir = rtl_epon_sla_u16(queue + 7) * 8;
			weights[row] = data[1] ? queue[9] : 0;
		}
		ret = rtl_epon_sla_bucket(io, port * 8 + row, pir, 1);
		if (!ret)
			ret = rtl_epon_sla_bucket(io, port * 8 + row, cir, 0);
		if (ret)
			return ret;
	}
	/* Stock AAL couples the first two WRR weights even when queue1 is off. */
	if (queues == 1)
		weights[1] = weights[0];
	ret = rtl_itu_puc_schedule(io, port, 0, weights);
	if (ret)
		return ret;
	io->write(io->context, valid_reg, (io->read(io->context, valid_reg) & ~(255U << shift)) |
		((1U << queues) - 1) << shift);
	return rtl_itu_puc_tcont_weights(io, port, (1U << queues) - 1, weights);
}

#endif
