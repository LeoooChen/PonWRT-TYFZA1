/* SPDX-License-Identifier: GPL-2.0-only */
/* Original UMM1001M_regInit and onuinfo DBA setup, ASIC9690. */
#ifndef RTL9617C_FTTR_CORE_H
#define RTL9617C_FTTR_CORE_H

#include "rtl9617c-fttr-reg.h"

static inline int rtl_fttr_core_init(const struct rtl_fttr_reg_io *io)
{
	static const struct {
		unsigned int first, last;
		bool clear;
	} ranges[] = {
		{ 0x20480, 0x20482, false }, { 0x20880, 0x20882, false },
		{ 0x30040, 0x30045, false }, { 0x30080, 0x30093, false },
		{ 0x300c0, 0x300ca, false }, { 0x300d0, 0x300da, false },
		{ 0x31000, 0x31fff, false }, { 0x32000, 0x32fff, false },
		{ 0x40042, 0x40043, false }, { 0x40080, 0x4008d, false },
		{ 0x40300, 0x4033f, false },
		{ 0x40400, 0x4047f, true }, { 0x40800, 0x408ff, true },
		/* The original profile API addresses 64 ONUs x 8 T-CONTs x 4 words. */
		{ 0x41000, 0x417ff, true }, { 0x41800, 0x418ff, false },
		{ 0x42000, 0x423ff, true }, { 0x43000, 0x43ffe, true },
	};
	unsigned int range, reg, pending;
	int ret;

#define FTTR_CORE_WRITE(address, value) do { \
	ret = io->write(io->context, address, value); if (ret) return ret; \
} while (0)
	FTTR_CORE_WRITE(0x40001, 0x30);
	FTTR_CORE_WRITE(0x40002, 0x22);
	ret = rtl_fttr_masked_write(io, 0x40022, 0xf, 0);
	if (ret)
		return ret;
	FTTR_CORE_WRITE(0x40011, 0x250);
	FTTR_CORE_WRITE(0x80, 0x249249);
	ret = rtl_fttr_masked_write(io, 0x30004, 0xffff, 0x8888);
	if (ret)
		return ret;
	ret = rtl_fttr_masked_write(io, 0x4001f, 0xffff, 250);
	if (ret)
		return ret;
	for (range = 0; range < ARRAY_SIZE(ranges); range++)
		for (reg = ranges[range].first; reg <= ranges[range].last; reg++) {
			ret = ranges[range].clear ? io->write(io->context, reg, 0) :
				io->read(io->context, reg, &pending);
			if (ret)
				return ret;
		}
	ret = io->read(io->context, 0x40068, &pending);
	if (ret)
		return ret;
	if (pending) {
		FTTR_CORE_WRITE(0x40060, pending);
	}
#undef FTTR_CORE_WRITE
	return 0;
}

static inline int rtl_fttr_dba_defaults(const struct rtl_fttr_reg_io *io)
{
	static const struct {
		unsigned int reg, mask, value;
	} settings[] = {
		{ 0x40021, 0xffff, 19440 },
		{ 0x40020, 0xff000000, 38U << 24 },
		{ 0x40020, 0x00ff0000, 38U << 16 },
		{ 0x40020, 0x0000ff00, 10U << 8 },
		{ 0x40020, 0x000000ff, 10 },
	};
	unsigned int i;
	int ret;

	for (i = 0; i < ARRAY_SIZE(settings); i++) {
		ret = rtl_fttr_masked_write(io, settings[i].reg, settings[i].mask, settings[i].value);
		if (ret)
			return ret;
	}
	return 0;
}

static inline int rtl_fttr_tcont_program(const struct rtl_fttr_reg_io *io,
		unsigned int id, unsigned int tcont, unsigned int fixed,
		unsigned int assured, unsigned int maximum)
{
	unsigned int base = 0x41000 + id * 32 + tcont * 4;
	int ret;

	/* Preserve the original intermediate value while installing the fixed grant. */
	ret = io->write(io->context, base, 0x14bf0);
	if (ret)
		return ret;
	ret = io->write(io->context, base + 1, fixed);
	return ret ? ret : io->write(io->context, base, assured << 16 | maximum);
}

/* Original registration callback: SetOnuTcontDba(ONU, 0, 0, 3840, 3840). */
static inline int rtl_fttr_management_grant(const struct rtl_fttr_reg_io *io,
					  unsigned int id, bool enable)
{
	unsigned int base = 0x41000 + id * 32;
	int ret = io->write(io->context, base, enable ? 0x1003e : 0);

	if (ret)
		return ret;
	ret = io->write(io->context, base + 1, 0);
	if (ret || !enable)
		return ret;
	return io->write(io->context, base, 0x3c003c);
}

#endif
