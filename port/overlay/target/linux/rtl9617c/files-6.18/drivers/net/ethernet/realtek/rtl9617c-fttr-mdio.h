/* SPDX-License-Identifier: GPL-2.0-only */
/* TYFZA1 rtk-taurus_mdio.ko: C22 register tunnel and table completion. */
#ifndef RTL9617C_FTTR_MDIO_H
#define RTL9617C_FTTR_MDIO_H

enum rtl_fttr_backend {
	RTL_FTTR_FPGA1 = 1,
	RTL_FTTR_FPGA2,
	RTL_FTTR_ASIC9690,
};

struct rtl_fttr_mdio_io {
	void *context;
	int (*read)(void *context, unsigned int reg);
	int (*write)(void *context, unsigned int reg, unsigned short value);
	void (*delay)(void *context, unsigned int usec);
};

static inline int rtl_fttr_raw_read(const struct rtl_fttr_mdio_io *io,
				  unsigned int reg, unsigned int *value)
{
	int low, high, ret;

	ret = io->write(io->context, 16, reg);
	if (ret)
		return ret;
	ret = io->write(io->context, 17, (reg >> 16) | 0x8000);
	if (ret)
		return ret;
	low = io->read(io->context, 20);
	if (low < 0)
		return low;
	high = io->read(io->context, 21);
	if (high < 0)
		return high;
	*value = (unsigned int)high << 16 | low;
	return 0;
}

static inline int rtl_fttr_raw_write(const struct rtl_fttr_mdio_io *io,
				   unsigned int reg, unsigned int value)
{
	int ret;

	ret = io->write(io->context, 16, reg);
	if (ret)
		return ret;
	ret = io->write(io->context, 17, (reg >> 16) & 0x7fff);
	if (ret)
		return ret;
	ret = io->write(io->context, 18, value);
	return ret ? ret : io->write(io->context, 19, value >> 16);
}

/* FPGA1 returns table data on the target read; newer engines use result ports.
 * The caller holds the MDIO bus lock across the complete transaction.
 */
static inline int rtl_fttr_reg_xfer(const struct rtl_fttr_mdio_io *io,
				  enum rtl_fttr_backend backend, unsigned int reg,
				  unsigned int *value, int write)
{
	unsigned int ready, result = 0, status, data = 0;
	unsigned int attempt, attempts;
	int ret;

	if (reg >= 0x40400 && reg <= 0x4047f) {
		ready = 2;
		result = 0x40072;
	} else if (reg >= 0x40800 && reg <= 0x408ff) {
		ready = 1;
		result = 0x40073;
	} else if (backend != RTL_FTTR_FPGA1 && reg >= 0x41000 && reg <= 0x417ff) {
		ready = 4;
		result = 0x40074;
	} else {
		return write ? rtl_fttr_raw_write(io, reg, *value) :
			rtl_fttr_raw_read(io, reg, value);
	}

	attempts = backend == RTL_FTTR_FPGA1 ? 30 : 2;
	for (attempt = 0; attempt < attempts; attempt++) {
		if (!attempt || backend == RTL_FTTR_FPGA1) {
			ret = write ? rtl_fttr_raw_write(io, reg, *value) :
				rtl_fttr_raw_read(io, reg, &data);
			if (ret)
				return ret;
		}
		ret = rtl_fttr_raw_read(io, 0x40040, &status);
		if (ret)
			return ret;
		if (status & ready) {
			if (write)
				return 0;
			if (backend != RTL_FTTR_FPGA1)
				return rtl_fttr_raw_read(io, result, value);
			*value = data;
			return 0;
		}
		if (attempt + 1 < attempts)
			io->delay(io->context, backend == RTL_FTTR_FPGA1 ? 50 : 600);
	}
	return -ETIMEDOUT;
}

static inline int rtl_fttr_identify(const struct rtl_fttr_mdio_io *io,
				  enum rtl_fttr_backend *backend)
{
	unsigned int value;
	int ret = rtl_fttr_raw_read(io, 0xc00044, &value);

	if (ret)
		return ret;
	if ((value & 0xffff) == 0x9690) {
		*backend = RTL_FTTR_ASIC9690;
		return 0;
	}
	ret = rtl_fttr_raw_read(io, 3, &value);
	if (ret)
		return ret;
	if (value <= 1)
		*backend = RTL_FTTR_FPGA1;
	else if (value == 2)
		*backend = RTL_FTTR_FPGA2;
	else
		return -ENODEV;
	return 0;
}

#endif
