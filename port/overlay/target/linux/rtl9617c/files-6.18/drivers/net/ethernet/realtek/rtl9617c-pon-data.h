/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_DATA_H
#define RTL9617C_PON_DATA_H

/* PDC address low6 identifies an LLID table entry, not necessarily its MAC
 * instance. A valid unicast entry supplies its actual MAC index in bits28:24.
 * Multicast LLIDs enter the primary business interface. */
static inline unsigned int rtl_pon_data_source(unsigned int index, unsigned int entry)
{
	if ((entry & (1U << 23)) && !(entry & (1U << 22)))
		return 32 + ((entry >> 24) & 31);
	if (entry & (1U << 22))
		return 32;
	return 32 + (index < 32 ? index : 0);
}
#endif
