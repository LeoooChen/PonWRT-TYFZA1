/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_NI_TABLE_H
#define RTL9617C_NI_TABLE_H

/* NI destination map and QM AXI attributes each have one shared staging
 * window. The lock covers idle wait, staging, command, result and readback.
 * Called from sleepable configuration paths; never calls FE/radio owners. */
int rtl9617c_ni_table(void *context,
	unsigned int (*read)(void *, unsigned int),
	void (*write)(void *, unsigned int, unsigned int),
	int (*indirect)(void *, unsigned int, unsigned int),
	unsigned int access, unsigned int index, unsigned int *result,
	const unsigned int *value, unsigned int count);
#endif
