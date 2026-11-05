/* SPDX-License-Identifier: GPL-2.0-only */
/* Original lib/modules/users/sloader.sh, after the FPGA DONE handshake. */
#ifndef RTL9617C_FTTR_FPGA_H
#define RTL9617C_FTTR_FPGA_H

#include "rtl9617c-fttr-mdio.h"
#include "rtl9617c-fttr-reg.h"

static inline int rtl_fttr_fpga_auth_needed(const struct rtl_fttr_reg_io *io,
		enum rtl_fttr_backend backend)
{
	unsigned int version;
	int ret;

	if (backend != RTL_FTTR_FPGA1)
		return 0;
	ret = io->read(io->context, 0, &version);
	if (ret)
		return ret;
	if (version == 0xffffffff)
		return -EIO;
	/* onuinfo_fpga 4122f8 enables authentication from version23040700. */
	return version > 0x230406ff;
}

static inline int rtl_fttr_fpga_auth(const struct rtl_fttr_reg_io *io,
		const unsigned char *ciphertext, unsigned int size)
{
	unsigned int i, value;
	int ret;

	if (size != 32)
		return -EMSGSIZE;
	/* libasicdrv drv_set_auth_ciphertext consumes native little-endian u32s. */
	for (i = 0; i < 8; i++) {
		value = ciphertext[i * 4] | (unsigned int)ciphertext[i * 4 + 1] << 8 |
			(unsigned int)ciphertext[i * 4 + 2] << 16 |
			(unsigned int)ciphertext[i * 4 + 3] << 24;
		ret = io->write(io->context, 0x57 - i, value);
		if (ret)
			return ret;
	}
	io->delay(io->context, 1200000);
	ret = io->read(io->context, 0x5e, &value);
	if (ret)
		return ret;
	if (value == 0xffffffff)
		return -EIO;
	return value & 1 ? -EACCES : 0;
}

static inline int rtl_fttr_fpga_init(const struct rtl_fttr_reg_io *io,
		enum rtl_fttr_backend backend, int has_calibration)
{
	const unsigned int regs[] = { 0x40001, 0x40002, 0x40022, 0x40023, 0x10 };
	const unsigned int values[] = { 0x25, 0x22, 5, 0x100000, 1 };
	unsigned int i;
	int ret;

	if (backend == RTL_FTTR_FPGA2)
		return 0;
	if (backend != RTL_FTTR_FPGA1)
		return -EOPNOTSUPP;
	for (i = has_calibration ? 0 : 3; i < 5; i++) {
		ret = io->write(io->context, regs[i], values[i]);
		if (ret)
			return ret;
		if (i < 4)
			io->delay(io->context, 1000);
	}
	return 0;
}

static inline int rtl_fttr_tx_enable(const struct rtl_fttr_reg_io *io,
		enum rtl_fttr_backend backend, int enabled)
{
	if (backend == RTL_FTTR_ASIC9690)
		return io->write(io->context, 0x20, !enabled);
	if (backend != RTL_FTTR_FPGA1 && backend != RTL_FTTR_FPGA2)
		return -EOPNOTSUPP;
	/* FPGA drv_gpon_trx_set changes only bit0 through bit_set/bit_cls. */
	return rtl_fttr_masked_write(io, 0x20, 1, !enabled);
}

#endif
