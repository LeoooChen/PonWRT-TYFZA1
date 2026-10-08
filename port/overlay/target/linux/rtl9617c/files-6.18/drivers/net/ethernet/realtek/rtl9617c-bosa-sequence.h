/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Semtech MCU calibration sequence, checked against TYFZA1's rtkbosa ELF. */
#ifndef RTL9617C_BOSA_SEQUENCE_H
#define RTL9617C_BOSA_SEQUENCE_H

enum rtl_bosa_model {
	RTL_BOSA_GN25L95 = 201,
	RTL_BOSA_GN25L98,
	RTL_BOSA_GN28L95,
	RTL_BOSA_GN28L96,
	RTL_BOSA_GN28L97,
};

struct rtl_bosa_io {
	void *context;
	int (*write)(void *context, unsigned int reg, unsigned char value);
	int (*read)(void *context, unsigned int reg, unsigned char *value);
	int (*wait)(void *context, unsigned int reg, unsigned char mask, unsigned char value);
};

static inline int rtl_bosa_range(const struct rtl_bosa_io *io, const unsigned char *cal,
				unsigned int offset, unsigned int first, unsigned int last)
{
	unsigned int reg;
	int ret;

	for (reg = first; reg <= last; reg++) {
		ret = io->write(io->context, reg, cal[offset + reg - first]);
		if (ret)
			return ret;
	}
	return 0;
}

static inline int rtl_bosa_modify(const struct rtl_bosa_io *io, unsigned int reg,
				 unsigned char mask, unsigned char value)
{
	unsigned char register_value;
	int ret = io->read(io->context, reg, &register_value);

	return ret ? ret : io->write(io->context, reg, (register_value & ~mask) | value);
}

static inline int rtl_bosa_calibrate(const struct rtl_bosa_io *io,
				    const unsigned char *cal, enum rtl_bosa_model model)
{
	unsigned int i;
	unsigned char value;
	int ret;

#define BOSA_WRITE(reg, val) do { ret = io->write(io->context, reg, val); if (ret) return ret; } while (0)
#define BOSA_RANGE(offset, first, last) do { ret = rtl_bosa_range(io, cal, offset, first, last); if (ret) return ret; } while (0)
#define BOSA_READ(reg) do { ret = io->read(io->context, reg, &value); if (ret) return ret; } while (0)
#define BOSA_MODIFY(reg, mask, val) do { ret = rtl_bosa_modify(io, reg, mask, val); if (ret) return ret; } while (0)

	if (model == RTL_BOSA_GN28L96 || model == RTL_BOSA_GN28L97) {
		BOSA_WRITE(0x7f, 0x81);
		BOSA_WRITE(0x89, 0);
		ret = io->wait(io->context, 0x89, 2, 2);
		if (ret)
			return ret;
		BOSA_RANGE(0x100, 0, 0x3f);
		BOSA_WRITE(0x6f, cal[0x16f]);
		BOSA_RANGE(0x170, 0x70, 0x77);
		BOSA_WRITE(0x7f, 0);
		BOSA_RANGE(0x1f8, 0xf8, 0xfd);
		BOSA_WRITE(0x7f, 0x82);
		BOSA_RANGE(0x300, 0x80, 0xff);
		BOSA_WRITE(0x7f, 0x83);
		BOSA_RANGE(0x380, 0x80, 0xff);
		BOSA_WRITE(0x7f, 0x80);
		BOSA_RANGE(0x200, 0x80, 0xff);
		BOSA_WRITE(0x7f, 0x81);
		for (i = 0x80; i <= 0xff; i++) {
			if (i == 0x88 || i == 0x8d || i == 0x8a)
				continue;
			BOSA_WRITE(i, cal[0x280 + i - 0x80]);
		}
		BOSA_WRITE(0x6e, cal[0x16e]);
		BOSA_READ(0x6e);
		BOSA_WRITE(0x88, cal[0x288]);
	} else if (model >= RTL_BOSA_GN25L95 && model <= RTL_BOSA_GN28L95) {
		BOSA_WRITE(0x7f, 4);
		BOSA_RANGE(0x280, 0x80, 0xff);
		BOSA_WRITE(0x7f, 5);
		BOSA_RANGE(0x300, 0x80, 0xff);
		BOSA_WRITE(0x7f, 6);
		BOSA_RANGE(0x380, 0x80, 0xff);
		BOSA_WRITE(0x7f, 2);
		BOSA_RANGE(0x200, 0x80, 0xff);
		BOSA_WRITE(0x7f, 2);
		BOSA_RANGE(0x252, 0xd2, 0xe7);
		BOSA_RANGE(0x100, 0, 0x4f);
		BOSA_WRITE(0x6f, cal[0x16f]);
		BOSA_WRITE(0x72, cal[0x172]);
		BOSA_WRITE(0x78, cal[0x178]);
		BOSA_WRITE(0x79, cal[0x179]);
		BOSA_WRITE(0x7f, 1);
		BOSA_WRITE(0xf8, cal[0x1f8]);
		BOSA_MODIFY(0xf9, 0xc0, 0xc0);
		BOSA_WRITE(0xfc, cal[0x1fc]);
		BOSA_MODIFY(0xfd, 0xc0, 0xc0);
		BOSA_READ(0x6e);
		BOSA_WRITE(0x6e, value | 0x40);
		BOSA_WRITE(0x6e, value & ~0x40);
		BOSA_WRITE(0x7f, 2);
		BOSA_WRITE(model == RTL_BOSA_GN25L95 ? 0xa0 : model == RTL_BOSA_GN25L98 ? 0x98 : 0x88, 0x6a);
		for (i = 0x7b; i <= 0x7e; i++)
			BOSA_WRITE(i, 0xff);
		BOSA_WRITE(0x7f, 2);
		BOSA_MODIFY(0xbb, 1, 0);
		BOSA_READ(0xbb);
		BOSA_WRITE(0x7f, 2);
		BOSA_READ(0xb1);
		BOSA_READ(0xb7);
	} else {
		return -22;
	}
#undef BOSA_WRITE
#undef BOSA_RANGE
#undef BOSA_READ
#undef BOSA_MODIFY
	return 0;
}

#endif
