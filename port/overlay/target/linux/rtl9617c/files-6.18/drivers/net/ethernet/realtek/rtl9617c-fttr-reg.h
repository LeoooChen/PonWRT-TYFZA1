/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_REG_H
#define RTL9617C_FTTR_REG_H

struct rtl_fttr_reg_io {
	void *context;
	int (*read)(void *context, unsigned int reg, unsigned int *value);
	int (*write)(void *context, unsigned int reg, unsigned int value);
	void (*delay)(void *context, unsigned int usec);
};

/* Original FPGA_reg_writeMasked writes even when the masked value is unchanged. */
static inline int rtl_fttr_masked_write(const struct rtl_fttr_reg_io *io,
		unsigned int reg, unsigned int mask, unsigned int value)
{
	unsigned int old;
	int ret = io->read(io->context, reg, &old);

	return ret ? ret : io->write(io->context, reg, (old & ~mask) | (value & mask));
}

#endif
