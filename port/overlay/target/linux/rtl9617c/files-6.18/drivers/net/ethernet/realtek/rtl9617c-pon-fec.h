/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_FEC_H
#define RTL9617C_PON_FEC_H
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-qm-deepq.h"

/* Stock aal_epon_fec_cfg_set and RTL8277C usFecState_set keep the
 * 1G encoder, payload scheduler and REPORT accounting consistent. */
static inline void rtl_pon_fec_set(const struct rtl_dq_io *io, enum rtl_pon_mode mode, int enable)
{
	unsigned int value;

	if (mode == RTL_PON_EPON_BI10G) {
		value = io->read(io->context, 0x24);
		io->write(io->context, 0x24, (value & ~1U) | !enable);
		return;
	}
	value = io->read(io->context, 0x810);
	io->write(io->context, 0x810, (value & ~2U) | (enable ? 2 : 0));
	value = io->read(io->context, 0x8050);
	io->write(io->context, 0x8050, (value & ~(1U << 20)) | (enable ? 1U << 20 : 0));
	value = io->read(io->context, 0x8054);
	io->write(io->context, 0x8054, (value & ~(1U << 20)) | (enable ? 1U << 20 : 0));
}
#endif
