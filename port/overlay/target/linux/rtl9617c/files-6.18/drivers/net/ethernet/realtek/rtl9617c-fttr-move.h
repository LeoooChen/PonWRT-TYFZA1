/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_MOVE_H
#define RTL9617C_FTTR_MOVE_H
#include "rtl9617c-fttr-onu.h"
#include "rtl9617c-fttr-core.h"

static inline int rtl_fttr_onu_serial_write(const struct rtl_fttr_reg_io *io,
		unsigned int id, const unsigned char serial[8])
{
	unsigned int word, value, observed, byte;
	int ret;

	/* Original local helper4733c/466bc: vendor word before serial word. */
	for (word = 0; word < 2; word++) {
		value = 0;
		for (byte = 0; byte < 4; byte++)
			value = value << 8 | serial[word * 4 + byte];
		ret = io->write(io->context, 0x40801 + id * 4 - word, value);
		if (ret)
			return ret;
		ret = io->read(io->context, 0x40801 + id * 4 - word, &observed);
		if (ret || observed != value)
			return ret ? ret : -EUCLEAN;
	}
	return 0;
}

/* Move the original hardware SN reservation. The caller withdraws FE/VLAN
 * ownership for both slots. This function is serialized with all MiniOLT
 * state/DBA commands; success means table update, not optical registration.
 */
static inline int rtl_fttr_onu_move(const struct rtl_fttr_reg_io *io,
		enum rtl_fttr_backend backend, unsigned int from, unsigned int to,
		const unsigned char serial[8], const unsigned char target_serial[8])
{
	struct rtl_fttr_onu source, target;
	const unsigned char empty[8] = { 0 };
	unsigned int i, id, slot, first, last, fixed, limits;
	int ret;

	if (from >= RTL_FTTR_ONUS || to >= RTL_FTTR_ONUS || from == to)
		return -EINVAL;
	if (backend < RTL_FTTR_FPGA1 || backend > RTL_FTTR_ASIC9690)
		return -EOPNOTSUPP;
	ret = rtl_fttr_onu_read(io, from, &source);
	if (!ret)
		ret = rtl_fttr_onu_read(io, to, &target);
	if (ret)
		return ret;
	for (i = 0; i < 8; i++)
		if (source.serial[i] != serial[i] || target.serial[i] != target_serial[i])
			return -ESTALE;
	if (!(source.downstream & BIT(18)) || (source.downstream & BIT(16)))
		return -ESTALE;
	if (target.downstream & (BIT(18) | BIT(16)))
		return -EBUSY;
	first = backend == RTL_FTTR_FPGA1 ? 0 : 1;
	last = backend == RTL_FTTR_FPGA1 ? 0 : 7;
	for (i = 0; i < 2; i++) {
		id = i ? to : from;
		for (slot = first; slot <= last; slot++) {
			ret = rtl_fttr_tcont_read(io, backend, id, slot, &fixed, &limits);
			if (ret)
				return ret;
			if (fixed || limits)
				return -EBUSY;
		}
	}
	/* The grant scan can take many MDIO transactions. Recheck both slots
	 * immediately before the first write; hardware registration is external
	 * to the driver's control mutex.
	 */
	ret = rtl_fttr_onu_read(io, from, &source);
	if (!ret)
		ret = rtl_fttr_onu_read(io, to, &target);
	if (ret)
		return ret;
	for (i = 0; i < 8; i++)
		if (source.serial[i] != serial[i] || target.serial[i] != target_serial[i])
			return -ESTALE;
	if (!(source.downstream & BIT(18)) || (source.downstream & BIT(16)))
		return -ESTALE;
	if (target.downstream & (BIT(18) | BIT(16)))
		return -EBUSY;
	/* Original mismatch path reserves the target, deactivates the source,
	 * then clears its SN. Its optional extra source deactivation is based
	 * on the target's old software OMCC flag; a live target is rejected here.
	 */
	ret = rtl_fttr_onu_serial_write(io, to, serial);
	if (!ret) {
		ret = rtl_fttr_onu_admin(io, backend, from, 1, serial);
		/* Identity changed after a target write: this is a partial move,
		 * not the preflight ESTALE that permits FE admission to resume.
		 */
		if (ret == -ESTALE)
			ret = -EUCLEAN;
	}
	if (!ret)
		ret = rtl_fttr_onu_serial_write(io, from, empty);
	return ret;
}

#endif
