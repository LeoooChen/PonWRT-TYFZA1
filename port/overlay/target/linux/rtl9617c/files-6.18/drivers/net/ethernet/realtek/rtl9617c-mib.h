/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_MIB_H
#define RTL9617C_MIB_H
#include "rtl9617c-qm-deepq.h"

#define RTL_CTC_PM_COUNTERS 37
#define RTL_NI_MIB_COUNTERS 52
#define RTL_EPON_MIB_COUNTERS 42

/* PM wire order is the board's eponoamd:0x412d44, downstream then upstream.
 * Counter RAM access/widths follow actual aal_ni_eth_port_mib_get and
 * aal_epon_mac_stats_port_eth_get. Reads do not clear hardware counters. */
static inline int rtl_ni_mib_read(const struct rtl_dq_io *io, unsigned int port,
	unsigned long long raw[RTL_NI_MIB_COUNTERS])
{
	unsigned int direction, index;
	int ret;

	for (direction = 0; direction < 2; direction++) {
		unsigned int access = direction ? 0xa1b0 : 0xa1a4;

		for (index = 0; index < 26; index++) {
			unsigned long long value = 0;

			if ((!direction && index == 24) || (direction && (index == 12 || index == 23 || index == 24))) {
				raw[direction * 26 + index] = 0;
				continue;
			}
			ret = io->indirect(io->context, access, (1U << 31) | (port << 5) | index);
			if (ret)
				return ret;
			value = io->read(io->context, access + 8);
			if (index == 25)
				value |= (unsigned long long)io->read(io->context, access + 4) << 32;
			raw[direction * 26 + index] = value;
		}
	}
	return 0;
}

static inline int rtl_epon_mib_read(const struct rtl_dq_io *io, unsigned int raw[RTL_EPON_MIB_COUNTERS])
{
	unsigned int index;
	int ret;

	io->write(io->context, 0x604, io->read(io->context, 0x604) & ~1U);
	for (index = 0; index < RTL_EPON_MIB_COUNTERS; index++) {
		unsigned int access = index < 21 ? 0x618 : 0x630;

		ret = io->indirect(io->context, access, (1U << 31) | (index % 21));
		if (ret)
			return ret;
		raw[index] = io->read(io->context, access + 4);
	}
	return 0;
}

static inline void rtl_mib_extend(unsigned long long *total, unsigned long long raw, int wide)
{
	if (wide)
		*total = raw;
	else
		*total += (unsigned int)((unsigned int)raw - (unsigned int)*total);
}

static inline void rtl_ni_mib_pm(unsigned long long total[RTL_NI_MIB_COUNTERS],
	const unsigned long long raw[RTL_NI_MIB_COUNTERS], unsigned long long pm[RTL_CTC_PM_COUNTERS])
{
	unsigned int index;
	const unsigned long long *rx = total, *tx = total + 26;

	for (index = 0; index < RTL_NI_MIB_COUNTERS; index++)
		rtl_mib_extend(total + index, raw[index], index % 26 == 25);
	for (index = 0; index < RTL_CTC_PM_COUNTERS; index++)
		pm[index] = 0;
	pm[1] = rx[13];
	pm[2] = tx[25];
	pm[3] = rx[25];
	pm[4] = tx[0] + tx[1] + tx[2];
	pm[5] = rx[0] + rx[1] + rx[2];
	pm[6] = tx[2];
	pm[7] = rx[2];
	pm[8] = tx[1];
	pm[9] = rx[1];
	pm[10] = tx[7];
	pm[11] = rx[8];
	pm[13] = rx[9];
	pm[14] = tx[8];
	pm[15] = rx[11];
	pm[17] = rx[10];
	pm[19] = rx[12];
	for (index = 0; index < 6; index++) {
		pm[20 + index] = tx[13 + index];
		pm[26 + index] = rx[15 + index];
	}
	pm[33] = rx[13];
	pm[34] = tx[7];
	pm[35] = rx[8];
}

static inline void rtl_epon_mib_pm(unsigned long long total[RTL_EPON_MIB_COUNTERS],
	const unsigned int raw[RTL_EPON_MIB_COUNTERS], unsigned long long pm[RTL_CTC_PM_COUNTERS])
{
	unsigned int index;
	const unsigned long long *rx = total, *tx = total + 21;

	for (index = 0; index < RTL_EPON_MIB_COUNTERS; index++) {
		if (index % 21 < 2)
			continue;
		rtl_mib_extend(total + index, raw[index], 0);
	}
	total[0] = (unsigned long long)raw[1] << 32 | raw[0];
	total[21] = (unsigned long long)raw[22] << 32 | raw[21];
	for (index = 0; index < RTL_CTC_PM_COUNTERS; index++)
		pm[index] = 0;
	pm[2] = rx[0];
	pm[3] = tx[0];
	pm[4] = rx[3] + rx[4] + rx[5];
	pm[5] = tx[3] + tx[4] + tx[5];
	pm[6] = rx[5];
	pm[7] = tx[5];
	pm[8] = rx[4];
	pm[9] = tx[4];
	pm[10] = rx[2];
	pm[11] = tx[2];
	pm[12] = rx[9];
	pm[13] = tx[9];
	pm[14] = rx[10];
	pm[15] = tx[10];
	for (index = 0; index < 6; index++) {
		pm[20 + index] = rx[11 + index];
		pm[26 + index] = tx[11 + index];
	}
	pm[34] = rx[2];
	pm[35] = tx[2];
}
#endif
