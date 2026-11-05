/* SPDX-License-Identifier: GPL-2.0-only */
/* Original lib/modules/users/sloader.sh, after the FPGA DONE handshake. */
#ifndef RTL9617C_FTTR_FPGA_H
#define RTL9617C_FTTR_FPGA_H

#include "rtl9617c-fttr-mdio.h"
#include "rtl9617c-fttr-reg.h"

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
