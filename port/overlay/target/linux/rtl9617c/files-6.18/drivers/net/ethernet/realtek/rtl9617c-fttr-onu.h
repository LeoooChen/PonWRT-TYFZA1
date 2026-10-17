/* SPDX-License-Identifier: GPL-2.0-only */
/* Original libponrelay ONU_Online / serial helper and libasicdrv state getters. */
#ifndef RTL9617C_FTTR_ONU_H
#define RTL9617C_FTTR_ONU_H

#include "rtl9617c-fttr-reg.h"

#define RTL_FTTR_ONUS 64

struct rtl_fttr_onu {
	unsigned int downstream, upstream, eqd;
	unsigned char serial[8];
};

static inline int rtl_fttr_onu_read(const struct rtl_fttr_reg_io *io,
				   unsigned int id, struct rtl_fttr_onu *onu)
{
	unsigned int vendor, serial, i;
	int ret;

	ret = io->read(io->context, 0x40401 + 2 * id, &onu->downstream);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x40803 + 4 * id, &onu->upstream);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x40801 + 4 * id, &vendor);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x40800 + 4 * id, &serial);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x40802 + 4 * id, &onu->eqd);
	if (ret)
		return ret;
	for (i = 0; i < 4; i++) {
		onu->serial[i] = vendor >> (24 - 8 * i);
		onu->serial[i + 4] = serial >> (24 - 8 * i);
	}
	return 0;
}

static inline int rtl_fttr_onu_block(const struct rtl_fttr_reg_io *io,
				    unsigned int id, bool blocked)
{
	unsigned int reg = 0x40401 + 2 * id, value;
	int ret = io->read(io->context, reg, &value);

	if (ret)
		return ret;
	value = (value & ~BIT(16)) | (blocked ? BIT(16) : 0);
	return io->write(io->context, reg, value);
}

static inline int rtl_fttr_ploam_send(const struct rtl_fttr_reg_io *io,
				     const unsigned int words[3], unsigned int repeat)
{
	unsigned int message, attempt, status, word;
	int ret;

	for (message = 0; message < repeat; message++) {
		for (attempt = 0; attempt < 2000; attempt++) {
			ret = io->read(io->context, 0x40007, &status);
			if (ret)
				return ret;
			if (!(status & BIT(1)))
				break;
		}
		if (attempt == 2000)
			return -ETIMEDOUT;
		ret = io->write(io->context, 0x40007, 0);
		if (ret)
			return ret;
		for (word = 0; word < 3; word++) {
			ret = io->write(io->context, 0x40008 + word, words[word]);
			if (ret)
				return ret;
		}
		ret = io->write(io->context, 0x40007, 1);
		if (ret)
			return ret;
	}
	return 0;
}

#endif
