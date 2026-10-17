/* SPDX-License-Identifier: GPL-2.0-only */
/* libasicdrv.so drv_fpga_exti2c_{read,write}_reg, physical I2C channel zero. */
#ifndef RTL9617C_FTTR_I2C_H
#define RTL9617C_FTTR_I2C_H

struct rtl_fttr_reg_io {
	void *context;
	int (*read)(void *context, unsigned int reg, unsigned int *value);
	int (*write)(void *context, unsigned int reg, unsigned int value);
	void (*delay)(void *context, unsigned int usec);
};

static inline int rtl_fttr_i2c_wait(const struct rtl_fttr_reg_io *io,
				  unsigned int reg, unsigned int mask)
{
	unsigned int value, attempt;
	int ret;

	for (attempt = 0; attempt < 10; attempt++) {
		ret = io->read(io->context, reg, &value);
		if (ret)
			return ret;
		if (!(value & mask))
			return 0;
		if (attempt < 9)
			io->delay(io->context, 200);
	}
	return -ETIMEDOUT;
}

static inline int rtl_fttr_i2c_xfer(const struct rtl_fttr_reg_io *io,
				  unsigned int slave, unsigned int reg,
				  unsigned int *value, int word, int read)
{
	unsigned int status;
	int ret = io->read(io->context, 0x78, &status);

#define FTTR_I2C_WRITE(address, data) do { \
	ret = io->write(io->context, address, data); if (ret) return ret; \
} while (0)
	if (ret)
		return ret;
	if (status)
		FTTR_I2C_WRITE(0x78, 1);
	FTTR_I2C_WRITE(0x70, 0);
	FTTR_I2C_WRITE(0x7a, slave << 1 | !!read);
	FTTR_I2C_WRITE(0x7b, reg);
	if (!read)
		FTTR_I2C_WRITE(0x7d, *value);
	FTTR_I2C_WRITE(0x79, 1 | (word ? 2 : 0));
	ret = rtl_fttr_i2c_wait(io, 0x79, 1);
	if (ret)
		return ret;
	ret = rtl_fttr_i2c_wait(io, 0x78, ~0U);
	if (ret)
		return ret;
	if (read) {
		ret = io->read(io->context, 0x7c, &status);
		if (ret)
			return ret;
		*value = status & (word ? 0xffff : 0xff);
	}
#undef FTTR_I2C_WRITE
	return 0;
}

#endif
