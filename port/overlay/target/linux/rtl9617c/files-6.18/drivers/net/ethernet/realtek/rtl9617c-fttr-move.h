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

/* Resume only prefixes of the recorded original write order. This entry is
 * separate from ordinary migration and requires the controller's durable
 * reservation-stage intent and withdrawn local forwarding ownership. */
static inline int rtl_fttr_onu_move_prefix(const struct rtl_fttr_onu *source,
 const struct rtl_fttr_onu *target, const unsigned char serial[8],
 const unsigned char previous[8])
{
 unsigned int i;
 bool original = true, half_clear = true, empty = true, reserved = true, half_target = true;
 for (i = 0; i < 8; i++) {
  original &= source->serial[i] == serial[i];
  half_clear &= source->serial[i] == (i < 4 ? 0 : serial[i]);
  empty &= source->serial[i] == 0;
  reserved &= target->serial[i] == serial[i];
  half_target &= target->serial[i] == (i < 4 ? serial[i] : previous[i]);
 }
 if (target->downstream & BIT(16)) return -EBUSY;
 if (empty && reserved && (!(source->downstream & BIT(18)) || (source->downstream & BIT(16)))) return 3;
 /* A registered target may already receive traffic under a different
  * registration context. It cannot be repaired by rewriting its SN. */
 if (target->downstream & BIT(18)) return -EBUSY;
 if (original && reserved && (source->downstream & (BIT(18) | BIT(16)))) return 1;
 if (original && half_target && (source->downstream & BIT(18)) && !(source->downstream & BIT(16))) return 0;
 if (half_clear && reserved && (source->downstream & BIT(16))) return 2;
 return -ESTALE;
}

static inline int rtl_fttr_onu_move_recover(const struct rtl_fttr_reg_io *io,
 enum rtl_fttr_backend backend, unsigned int from, unsigned int to,
 const unsigned char serial[8], const unsigned char previous[8])
{
 struct rtl_fttr_onu source, target, check_source, check_target;
 unsigned int i, slot, last, fixed, limits;
 const unsigned char empty[8] = {0};
 int ret, prefix;
 if (from >= RTL_FTTR_ONUS || to >= RTL_FTTR_ONUS || from == to) return -EINVAL;
 if (backend < RTL_FTTR_FPGA1 || backend > RTL_FTTR_ASIC9690) return -EOPNOTSUPP;
 for (i = 0; i < 8 && !serial[i]; i++);
 if (i == 8) return -EINVAL;
 ret = rtl_fttr_onu_read(io, from, &source);
 if (!ret) ret = rtl_fttr_onu_read(io, to, &target);
 if (ret) return ret;
 prefix = rtl_fttr_onu_move_prefix(&source, &target, serial, previous);
 if (prefix < 0) return prefix;
 last = backend == RTL_FTTR_FPGA1 ? 0 : 7;
 for (i = 0; i < 2; i++)
  for (slot = backend == RTL_FTTR_FPGA1 ? 0 : 1; slot <= last; slot++) {
   ret = rtl_fttr_tcont_read(io, backend, i ? to : from, slot, &fixed, &limits);
   if (ret) return ret;
   if (fixed || limits) return -EBUSY;
  }
 ret = rtl_fttr_onu_read(io, from, &check_source);
 if (!ret) ret = rtl_fttr_onu_read(io, to, &check_target);
 if (ret) return ret;
 for (i = 0; i < 8; i++)
  if (source.serial[i] != check_source.serial[i] || target.serial[i] != check_target.serial[i]) return -ESTALE;
 if (source.downstream != check_source.downstream || target.downstream != check_target.downstream) return -ESTALE;
 if (prefix == 3) return 0;
 if (!prefix) {
  /* Vendor word is already written. Finish only the serial word. */
  unsigned int value = 0, observed;
  for (i = 4; i < 8; i++) value = value << 8 | serial[i];
  ret = io->write(io->context, 0x40800 + to * 4, value);
  if (!ret) ret = io->read(io->context, 0x40800 + to * 4, &observed);
  if (ret || observed != value) return ret ? ret : -EUCLEAN;
 }
 if (prefix < 2) {
  ret = rtl_fttr_onu_read(io, from, &source);
  if (!ret) ret = rtl_fttr_onu_read(io, to, &target);
  if (ret) return ret;
  if (rtl_fttr_onu_move_prefix(&source, &target, serial, previous) != 1) return -EUCLEAN;
  /* Blocking alone cannot prove that the deactivation mailbox was sent.
   * Repeat original state4, including FPGA US clear and PLOAM, before SN
   * deletion; do not infer completion from the downstream bit alone. */
  ret = rtl_fttr_onu_admin(io, backend, from, 1, serial);
  if (ret) return ret == -ESTALE ? -EUCLEAN : ret;
 }
 ret = rtl_fttr_onu_read(io, from, &source);
 if (!ret) ret = rtl_fttr_onu_read(io, to, &target);
 if (ret) return ret;
 int observed_prefix = rtl_fttr_onu_move_prefix(&source, &target, serial, previous);
 if (!(source.downstream & BIT(16)) || observed_prefix != (prefix == 2 ? 2 : 1)) return -EUCLEAN;
 if (prefix == 2) {
  unsigned int observed;
  ret = io->write(io->context, 0x40800 + from * 4, 0);
  if (!ret) ret = io->read(io->context, 0x40800 + from * 4, &observed);
  return ret ? ret : observed ? -EUCLEAN : 0;
 }
 return rtl_fttr_onu_serial_write(io, from, empty);
}

#endif
