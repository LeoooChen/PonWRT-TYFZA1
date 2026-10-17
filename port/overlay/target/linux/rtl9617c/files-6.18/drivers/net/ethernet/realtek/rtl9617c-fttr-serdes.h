/* SPDX-License-Identifier: GPL-2.0-only */
/* 9690 sequences from libasicdrv.so and its onuinfo caller. */
#ifndef RTL9617C_FTTR_SERDES_H
#define RTL9617C_FTTR_SERDES_H

#include "rtl9617c-fttr-reg.h"

struct rtl_fttr_field {
	unsigned int reg;
	unsigned char high, low;
	unsigned short value;
};

static inline int rtl_fttr_field_write(const struct rtl_fttr_reg_io *io,
		unsigned int reg, unsigned int high, unsigned int low, unsigned int value)
{
	unsigned int old, next;
	int ret = io->read(io->context, reg, &old);

	if (ret)
		return ret;
	next = (old & ~GENMASK(high, low)) | (value << low);
	return next == old ? 0 : io->write(io->context, reg, next);
}

static inline int rtl_fttr_fields(const struct rtl_fttr_reg_io *io,
		const struct rtl_fttr_field *fields, unsigned int count)
{
	unsigned int i;
	int ret;

	for (i = 0; i < count; i++) {
		ret = rtl_fttr_field_write(io, fields[i].reg, fields[i].high,
					 fields[i].low, fields[i].value);
		if (ret)
			return ret;
	}
	return 0;
}

/* FPGA_reg_writeMasked always writes, even if the masked value is unchanged. */
static inline int rtl_fttr_masked_write(const struct rtl_fttr_reg_io *io,
		unsigned int reg, unsigned int mask, unsigned int value)
{
	unsigned int old;
	int ret = io->read(io->context, reg, &old);

	return ret ? ret : io->write(io->context, reg, (old & ~mask) | (value & mask));
}

static inline int rtl_fttr_pll_wait(const struct rtl_fttr_reg_io *io,
		unsigned int reg, unsigned int bit, unsigned int delay, unsigned int attempts)
{
	unsigned int value, i;
	int ret;

	for (i = 0; i < attempts; i++) {
		io->delay(io->context, delay);
		ret = io->read(io->context, reg, &value);
		if (ret)
			return ret;
		if (value & BIT(bit))
			return 0;
	}
	return -ETIMEDOUT;
}

/* Keep each operation ordered and stop on its actual transport error. */
#define FTTR_SERDES_WRITE(reg, value) do { \
	ret = io->write(io->context, reg, value); if (ret) return ret; \
} while (0)
#define FTTR_SERDES_FIELD(reg, high, low, value) do { \
	ret = rtl_fttr_field_write(io, reg, high, low, value); if (ret) return ret; \
} while (0)

static inline int rtl_fttr_asic_mux(const struct rtl_fttr_reg_io *io)
{
	int ret;

	FTTR_SERDES_WRITE(0xc00018, 0x4e15);
	FTTR_SERDES_WRITE(0xc0001c, 0);
	FTTR_SERDES_WRITE(0xc00020, 0);
	FTTR_SERDES_WRITE(0xc00044, 0xa0000000);
	return 0;
}

/* Host mode 5 -> WTG HSGMII (1), host mode 9 -> WTG USXGMII (3). */
static inline int rtl_fttr_wtg_configure(const struct rtl_fttr_reg_io *io,
		unsigned int mode, bool nway)
{
	static const unsigned int patches[2][15] = {
		{ 0x07500502, 0x053286ff, 0x043238ee, 0x043301fd, 0x053c3fc0,
		  0x053a003f, 0x074b0190, 0x074c0190, 0x04010c00, 0x04010400,
		  0x04010c00, 0x0515f968, 0x050b2020, 0x05092584, 0x05072e00 },
		{ 0x05c13c30, 0x05c41e40, 0x05da5ae8, 0x07940007, 0x05f286ff,
		  0x043238fe, 0x043301fd, 0x05fc3fc0, 0x05fa003f, 0x05d5d269,
		  0x05cb2020, 0x05c74220, 0x04000030, 0x04000010, 0x04000030 },
	};
	unsigned int i;
	int ret;

	if (mode != 5 && mode != 9)
		return -EINVAL;
	FTTR_SERDES_WRITE(0x10, 0);
	io->delay(io->context, 100);
	FTTR_SERDES_WRITE(0x10, 1);
	FTTR_SERDES_WRITE(0x11, mode == 5 ? 6 : 7);
	if (mode == 9) {
		ret = rtl_fttr_masked_write(io, 0x30005, 0x10, 0x10);
		if (ret)
			return ret;
	}
	FTTR_SERDES_FIELD(0xc080f8, 3, 3, 0);
	FTTR_SERDES_FIELD(0xc080f8, 1, 1, 0);
	ret = rtl_fttr_pll_wait(io, 0xc08008, 6, 1, 2001);
	if (ret)
		return ret;
	if (mode == 5) {
		FTTR_SERDES_FIELD(0xc08028, 9, 8, 1);
		FTTR_SERDES_FIELD(0xc08028, 21, 20, 1);
		FTTR_SERDES_WRITE(0xc09000, nway ? 0x000271d0 : 0x000270d0);
		FTTR_SERDES_WRITE(0xc09004, 3);
	} else {
		for (i = 0; i < ARRAY_SIZE(patches[1]); i++) {
			FTTR_SERDES_WRITE(0xc09000, patches[1][i]);
			FTTR_SERDES_WRITE(0xc09004, 3);
		}
		FTTR_SERDES_FIELD(0xc08010, 19, 15, 0);
		FTTR_SERDES_FIELD(0xc08028, 9, 8, 0);
		FTTR_SERDES_FIELD(0xc08028, 21, 20, 1);
		FTTR_SERDES_WRITE(0xc09000, nway ? 0x00f10540 : 0x00f1054f);
		FTTR_SERDES_WRITE(0xc09004, 3);
	}
	FTTR_SERDES_FIELD(0xc08028, 14, 14, 1);
	FTTR_SERDES_FIELD(0xc08028, 13, 13, 1);
	FTTR_SERDES_FIELD(0xc08028, 12, 12, 1);
	FTTR_SERDES_FIELD(0xc08028, 10, 10, 1);
	FTTR_SERDES_FIELD(0xc08010, 4, 0, mode == 5 ? 0x12 : 0x0d);
	if (mode == 5) {
		for (i = 0; i < ARRAY_SIZE(patches[0]); i++) {
			FTTR_SERDES_WRITE(0xc09000, patches[0][i]);
			FTTR_SERDES_WRITE(0xc09004, 3);
		}
	}
	FTTR_SERDES_FIELD(0xc080f8, 3, 3, 1);
	FTTR_SERDES_FIELD(0xc080f8, 1, 1, 1);
	return 0;
}

static inline int rtl_fttr_pon_configure(const struct rtl_fttr_reg_io *io,
		unsigned int mode)
{
	static const struct rtl_fttr_field normal[] = {
		{ 0xc0c434, 12, 12, 0x1 },
		{ 0xc0c430, 11, 10, 0x1 },
		{ 0xc0c430, 9, 8, 0x2 },
		{ 0xc0c434, 5, 2, 0x1 },
		{ 0xc0c404, 15, 12, 0x3 },
		{ 0xc0c404, 11, 10, 0x3 },
		{ 0xc0c558, 5, 4, 0x2 },
		{ 0xc0c410, 12, 10, 0x7 },
		{ 0xc0c428, 3, 0, 0x0 },
		{ 0xc0c438, 3, 2, 0x1 },
		{ 0xc0c44c, 8, 8, 0x0 },
		{ 0xc0c468, 12, 10, 0x6 },
		{ 0xc0c468, 9, 7, 0x5 },
		{ 0xc0c510, 2, 0, 0x1 },
		{ 0xc0c494, 2, 0, 0x7 },
		{ 0xc0c514, 15, 15, 0x0 },
		{ 0xc0c534, 14, 14, 0x0 },
		{ 0xc0c510, 5, 3, 0x1 },
		{ 0xc0c530, 13, 13, 0x0 },
		{ 0xc0c53c, 7, 0, 0x80 },
		{ 0xc0c558, 1, 0, 0x2 },
		{ 0xc0c53c, 7, 0, 0x80 },
		{ 0xc0c468, 6, 4, 0x5 },
	};
	static const struct rtl_fttr_field burst[] = {
		{ 0xc0c53c, 7, 0, 0x80 },
		{ 0xc0c494, 2, 0, 0x7 },
	};
	static const struct rtl_fttr_field oversample[] = {
		{ 0xc0c434, 5, 2, 0x1 },
		{ 0xc0c404, 15, 12, 0x2 },
		{ 0xc0c428, 3, 0, 0x0 },
		{ 0xc0c438, 3, 2, 0x1 },
		{ 0xc0c44c, 8, 8, 0x0 },
		{ 0xc0c494, 2, 0, 0x7 },
		{ 0xc0c514, 15, 15, 0x0 },
		{ 0xc0c534, 5, 5, 0x1 },
		{ 0xc0c484, 13, 13, 0x1 },
		{ 0xc0c530, 13, 13, 0x0 },
		{ 0xc0c558, 1, 0, 0x2 },
		{ 0xc0c53c, 7, 0, 0x80 },
		{ 0xc0c454, 0, 0, 0x0 },
		{ 0xc0c4a4, 3, 3, 0x1 },
		{ 0xc0c4a4, 10, 6, 0x1 },
		{ 0xc0c4c8, 7, 0, 0xff },
		{ 0xc0c4c8, 9, 9, 0x0 },
		{ 0xc0c4c8, 15, 15, 0x0 },
		{ 0xc0c048, 3, 3, 0x1 },
		{ 0xc0c048, 5, 5, 0x1 },
		{ 0xc0c048, 7, 7, 0x1 },
		{ 0xc0c4f0, 5, 0, 0x0 },
		{ 0xc0c4f0, 12, 6, 0x0 },
		{ 0xc0c4e8, 11, 6, 0x0 },
		{ 0xc0c0a4, 15, 15, 0x0 },
		{ 0xc0c03c, 9, 9, 0x1 },
		{ 0xc0c48c, 2, 0, 0x0 },
		{ 0xc0c098, 8, 8, 0x1 },
		{ 0xc0c4f4, 6, 0, 0x7f },
		{ 0xc0c4c0, 2, 2, 0x0 },
		{ 0xc0c4c0, 11, 11, 0x1 },
		{ 0xc0c4c0, 14, 14, 0x1 },
		{ 0xc0c540, 14, 14, 0x0 },
		{ 0xc0c518, 13, 0, 0x432 },
	};
	unsigned int delay = mode == 3 ? 1000 : 1;
	unsigned int attempts = mode == 3 ? 101 : 2001;
	int ret;

	if (mode < 1 || mode > 3)
		return -EINVAL;
	if (mode == 2) {
		ret = rtl_fttr_masked_write(io, 0x40010, 0x10, 0x10);
		if (ret)
			return ret;
		ret = rtl_fttr_masked_write(io, 0x40018, 0xc00, 0x800);
		if (ret)
			return ret;
	}
	FTTR_SERDES_FIELD(0xc080f8, 2, 2, 0);
	FTTR_SERDES_FIELD(0xc080f8, 0, 0, 0);
	FTTR_SERDES_FIELD(0xc08004, 1, 1, 1);
	if (mode != 3) {
		FTTR_SERDES_FIELD(0xc0c538, 4, 3, 2);
		FTTR_SERDES_FIELD(0xc0c538, 7, 5, 1);
	}
	ret = rtl_fttr_pll_wait(io, 0xc0c5c0, 25, delay, attempts);
	if (ret)
		return ret;
	FTTR_SERDES_FIELD(0xc08004, 2, 2, 1);
	if (mode != 3) {
		FTTR_SERDES_FIELD(0xc0c044, 15, 6, 1);
		FTTR_SERDES_FIELD(0xc0cc04, 3, 3, 1);
	}
	FTTR_SERDES_FIELD(0xc00018, 0, 0, 1);
	if (mode == 1)
		ret = rtl_fttr_fields(io, normal, ARRAY_SIZE(normal));
	else if (mode == 2)
		ret = rtl_fttr_fields(io, burst, ARRAY_SIZE(burst));
	else
		ret = rtl_fttr_fields(io, oversample, ARRAY_SIZE(oversample));
	if (ret)
		return ret;
	FTTR_SERDES_FIELD(0xc08004, 0, 0, 1);
	ret = rtl_fttr_pll_wait(io, 0xc0c5c0, 24, delay, attempts);
	if (ret)
		return ret;
	if (mode == 3) {
		ret = rtl_fttr_pll_wait(io, 0xc0c5c0, 23, delay, 501);
		if (ret)
			return ret;
	} else {
		FTTR_SERDES_FIELD(0xc08004, 3, 3, 1);
	}
	ret = rtl_fttr_pll_wait(io, 0xc0c5c0, 22, delay, mode == 3 ? 501 : 2001);
	if (ret)
		return ret;
	if (mode != 3) {
		FTTR_SERDES_FIELD(0xc08004, 4, 4, 1);
	}
	FTTR_SERDES_WRITE(0xc0cc10, 0);
	FTTR_SERDES_WRITE(0xc0ca28, 0);
	FTTR_SERDES_FIELD(0xc0cc1c, 0, 0, 1);
	FTTR_SERDES_WRITE(0xc0cc18, 0xc);
	FTTR_SERDES_WRITE(0xc0cc18, 0);
	if (mode != 2) {
		FTTR_SERDES_WRITE(0xc0cc14, 6);
	}
	if (mode != 3) {
		FTTR_SERDES_WRITE(0xc0cc14, 0x586);
	}
	if (mode != 2) {
		FTTR_SERDES_WRITE(0xc0cc14, 0);
	}
	if (mode != 3) {
		FTTR_SERDES_WRITE(0xc0cc14, 0x580);
	}
	FTTR_SERDES_FIELD(0xc080f8, 2, 2, 1);
	FTTR_SERDES_FIELD(0xc080f8, 0, 0, 1);
	return 0;
}

#undef FTTR_SERDES_FIELD
#undef FTTR_SERDES_WRITE

#endif
