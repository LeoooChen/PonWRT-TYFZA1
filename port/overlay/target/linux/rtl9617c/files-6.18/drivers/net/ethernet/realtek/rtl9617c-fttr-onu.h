/* SPDX-License-Identifier: GPL-2.0-only */
/* Original libponrelay ONU_Online / serial helper and libasicdrv state getters. */
#ifndef RTL9617C_FTTR_ONU_H
#define RTL9617C_FTTR_ONU_H

#include "rtl9617c-fttr-reg.h"
#include "rtl9617c-fttr-mdio.h"

#define RTL_FTTR_ONUS 64

struct rtl_fttr_onu {
	unsigned int downstream, upstream, eqd;
	unsigned char serial[8];
};

static inline int rtl_fttr_onu_read(const struct rtl_fttr_reg_io *io,
				   unsigned int id, struct rtl_fttr_onu *onu)
{
	unsigned int vendor, serial, i;
	int ret;

	ret = io->read(io->context, 0x40401 + 2 * id, &onu->downstream);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x40803 + 4 * id, &onu->upstream);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x40801 + 4 * id, &vendor);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x40800 + 4 * id, &serial);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x40802 + 4 * id, &onu->eqd);
	if (ret)
		return ret;
	for (i = 0; i < 4; i++) {
		onu->serial[i] = vendor >> (24 - 8 * i);
		onu->serial[i + 4] = serial >> (24 - 8 * i);
	}
	return 0;
}

static inline int rtl_fttr_onu_block(const struct rtl_fttr_reg_io *io,
				    enum rtl_fttr_backend backend, unsigned int id, bool blocked)
{
	unsigned int reg = 0x40401 + 2 * id, value;
	int ret;

	if (backend != RTL_FTTR_ASIC9690) {
		/* libponrelay1p25g: replace the whole DS word (including the
		 * FPGA1 fixed grant), then clear US state after 10ms. The caller
		 * must withdraw its forwarding and DBA ownership first.
		 * reg_io already waits for the backend's table completion.
		 */
		ret = io->write(io->context, reg, blocked ? BIT(16) : 0);
		if (ret || !blocked)
			return ret;
		io->delay(io->context, 10000);
		return io->write(io->context, 0x40803 + 4 * id, 0);
	}
	ret = io->read(io->context, reg, &value);
	if (ret)
		return ret;
	value = (value & ~BIT(16)) | (blocked ? BIT(16) : 0);
	return io->write(io->context, reg, value);
}

static inline int rtl_fttr_ploam_send(const struct rtl_fttr_reg_io *io,
				     const unsigned int words[3], unsigned int word_count,
				     unsigned int repeat)
{
	unsigned int message, attempt, status, word;
	int ret;

	for (message = 0; message < repeat; message++) {
		for (attempt = 0; attempt < 2000; attempt++) {
			ret = io->read(io->context, 0x40007, &status);
			if (ret)
				return ret;
			if (!(status & BIT(1)))
				break;
		}
		if (attempt == 2000)
			return -ETIMEDOUT;
		ret = io->write(io->context, 0x40007, 0);
		if (ret)
			return ret;
		for (word = 0; word < word_count; word++) {
			ret = io->write(io->context, 0x40008 + word, words[word]);
			if (ret)
				return ret;
		}
		ret = io->write(io->context, 0x40007, 1);
		if (ret)
			return ret;
	}
	return 0;
}

/* Permanent deactivation (original state5) and explicit resume (state3).
 * The original state4 ASIC timer is a separate controller policy, not a
 * background driver action that may reopen a withdrawn ONU.
 */
static inline int rtl_fttr_onu_admin(const struct rtl_fttr_reg_io *io,
		enum rtl_fttr_backend backend, unsigned int id, bool blocked,
		const unsigned char serial[8])
{
	struct rtl_fttr_onu onu;
	unsigned int words[3] = { (id << 24) | 0x50000, 0, 0 }, i, status;
	int ret;

	if (id >= RTL_FTTR_ONUS || backend < RTL_FTTR_FPGA1 || backend > RTL_FTTR_ASIC9690)
		return -EINVAL;
	ret = rtl_fttr_onu_read(io, id, &onu);
	if (ret)
		return ret;
	for (i = 0; i < sizeof(onu.serial); i++)
		if (onu.serial[i] != serial[i])
			return -ESTALE;
	ret = rtl_fttr_onu_block(io, backend, id, blocked);
	if (ret)
		return ret;
	if (blocked) {
		/* FPGA deactivation writes only the first mailbox word. */
		ret = rtl_fttr_ploam_send(io, words, backend == RTL_FTTR_ASIC9690 ? 3 : 1, 1);
		if (ret)
			return ret;
	}
	ret = io->read(io->context, 0x40401 + 2 * id, &status);
	return ret ? ret : !!(status & BIT(16)) == blocked ? 0 : -EUCLEAN;
}

#endif
