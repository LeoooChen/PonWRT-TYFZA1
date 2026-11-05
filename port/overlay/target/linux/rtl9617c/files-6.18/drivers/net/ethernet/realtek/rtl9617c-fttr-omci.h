/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_OMCI_H
#define RTL9617C_FTTR_OMCI_H

#include "rtl9617c-fttr-mdio.h"
#include "rtl9617c-fttr-reg.h"

struct rtl_fttr_omci_link {
	unsigned char host[6], olt[6];
	unsigned short ethertype;
	bool downstream_length;
};

static inline int rtl_fttr_omci_init(const struct rtl_fttr_reg_io *io,
				    enum rtl_fttr_backend backend,
				    const unsigned char host[6])
{
	unsigned int value;
	int ret;

	if (backend < RTL_FTTR_FPGA1 || backend > RTL_FTTR_ASIC9690)
		return -EOPNOTSUPP;
	/* onuinfo programs the host's receive MAC before plr_fpga_init. */
	ret = io->write(io->context, 0x30001, host[0] << 8 | host[1]);
	if (ret)
		return ret;
	ret = io->write(io->context, 0x30000, (unsigned int)host[2] << 24 |
			(unsigned int)host[3] << 16 | host[4] << 8 | host[5]);
	if (ret)
		return ret;
	if (backend == RTL_FTTR_ASIC9690) {
		ret = io->read(io->context, 0x30004, &value);
		if (ret)
			return ret;
		if ((value & 0xffff) == 0x88a8) {
			ret = io->write(io->context, 0x30004, (value & 0xffff0000) | 0x8888);
			if (ret)
				return ret;
		}
	} else {
		/* libponrelay1p25g plr_fpga_init writes the whole register. */
		ret = io->write(io->context, 0x30004, 0x8888);
		if (ret)
			return ret;
	}
	ret = io->write(io->context, 0x40029,
		backend == RTL_FTTR_ASIC9690 ? 0xc140 : 0xc0c8);
	if (ret)
		return ret;
	ret = io->read(io->context, 0x4000c, &value);
	return ret ? ret : io->write(io->context, 0x4000c, value | BIT(3));
}

static inline int rtl_fttr_omci_link_read(const struct rtl_fttr_reg_io *io,
					enum rtl_fttr_backend backend,
					struct rtl_fttr_omci_link *link)
{
	unsigned int words[6], i;
	int ret;

	if (backend < RTL_FTTR_FPGA1 || backend > RTL_FTTR_ASIC9690)
		return -EOPNOTSUPP;
	for (i = 0; i < ARRAY_SIZE(words); i++) {
		ret = io->read(io->context, 0x30000 + i, &words[i]);
		if (ret)
			return ret;
	}
	link->host[0] = words[1] >> 8;
	link->host[1] = words[1];
	link->olt[0] = words[3] >> 8;
	link->olt[1] = words[3];
	for (i = 0; i < 4; i++) {
		link->host[i + 2] = words[0] >> (24 - 8 * i);
		link->olt[i + 2] = words[2] >> (24 - 8 * i);
	}
	link->ethertype = words[4];
	link->downstream_length = !!(words[5] & BIT(3));
	if (backend != RTL_FTTR_ASIC9690) {
		/* The FPGA relay's sender uses this fixed DA and 16-byte envelope,
		 * regardless of the source-MAC and length-mode register values. */
		link->olt[0] = 0;
		for (i = 1; i < 6; i++)
			link->olt[i] = 0x11;
		link->downstream_length = false;
	}
	return 0;
}

#endif
