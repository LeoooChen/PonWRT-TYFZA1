/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_EPON_PATH_H
#define RTL9617C_EPON_PATH_H
#include <linux/types.h>

/* PDC POL identifies the native MAC instance, not a wire LLID or ITU GEM.
 * A single immutable snapshot covers the PUC queue width and all LLID rows. */
struct rtl_epon_paths {
 u64 generation,registration_epoch;
 u32 active,table[64];
 unsigned int count,queues,mode;
 u8 queue_mask[32];
};
#endif
