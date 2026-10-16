/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_XGPON_RESET_H
#define RTL9617C_XGPON_RESET_H

#include "rtl9617c-xgtc-recovery.h"
#include "rtl9617c-xgpon-hw.h"

struct rtl_xgpon_mac_saved {
	struct rtl_xgtc_puc_saved puc;
	unsigned int upstream[256], downstream[256], pdc[256][2];
};

/* Original backup_ponmac covers GEM maps as well as the PUC scheduler state.
 * PDC additionally retains the native CPU/FE delivery policy across its reset. */
static inline int rtl_xgpon_mac_save(const struct rtl_dq_io *io,
		struct rtl_xgpon_mac_saved *saved)
{
	int ret = rtl_xgtc_puc_save(io, &saved->puc);

	if (ret)
		return ret;
	for (unsigned int i = 0; i < 256; i++) {
		ret = rtl_xgtc_table_read(io, 0x4618, 0x461c, i, &saved->upstream[i], 1);
		if (ret)
			return ret;
		saved->downstream[i] = io->read(io->context, 0x412c + i * 4);
		ret = rtl_xgtc_table_read(io, 0x9020, 0x9028, i, saved->pdc[i], 2);
		if (ret)
			return ret;
	}
	return 0;
}

static inline int rtl_xgpon_mac_restore(const struct rtl_dq_io *io,
		struct rtl_xgpon_mac_saved *saved)
{
	static const unsigned int pdc_mask[2] = {~0U, 0x3ffff};
	unsigned int valid[8];
	int ret;

	/* OMCC is available immediately; business VOQs await the activation handoff. */
	for (unsigned int i = 0; i < 8; i++) {
		valid[i] = saved->puc.valid[i];
		saved->puc.valid[i] &= i ? 0 : 255;
	}
	ret = rtl_xgtc_puc_restore(io, &saved->puc);
	for (unsigned int i = 0; i < 8; i++)
		saved->puc.valid[i] = valid[i];
	if (ret)
		return ret;
	for (unsigned int i = 0; i < 256; i++) {
		unsigned int us = saved->upstream[i], ds = saved->downstream[i];

		if (us & (1U << 16)) {
			ret = rtl_xgpon_upstream_gem_set(io, i, us & 65535, 1,
				(us >> 17) & 1, (us >> 18) & 1);
			if (ret)
				return ret;
		}
		if (ds & (1U << 17))
			rtl_xgpon_downstream_gem_set(io, i, ds & 65535, 1, (ds >> 16) & 1);
		ret = rtl_xgtc_table_restore(io, 0x9020, 0x9028, i,
			saved->pdc[i], pdc_mask, 2);
		if (ret)
			return ret;
	}
	return 0;
}

/* Rev-C random-load pulse in the original aal_xgpon_reset path. */
static inline void rtl_xgpon_random_load(const struct rtl_dq_io *io, unsigned int seed)
{
	unsigned int word = (io->read(io->context, 0x4a18) & 0x60000000U) |
		(seed & 0x1fffffffU);

	io->write(io->context, 0x4a18, word);
	io->write(io->context, 0x4a18, word | 0x80000000U);
	io->write(io->context, 0x4a18, word);
}

#endif
