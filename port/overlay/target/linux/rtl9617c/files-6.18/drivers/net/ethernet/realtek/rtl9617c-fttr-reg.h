/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_REG_H
#define RTL9617C_FTTR_REG_H

struct rtl_fttr_reg_io {
	void *context;
	int (*read)(void *context, unsigned int reg, unsigned int *value);
	int (*write)(void *context, unsigned int reg, unsigned int value);
	void (*delay)(void *context, unsigned int usec);
};

#endif
