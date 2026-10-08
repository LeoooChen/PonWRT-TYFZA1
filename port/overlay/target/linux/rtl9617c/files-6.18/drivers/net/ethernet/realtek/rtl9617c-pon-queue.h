/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_QUEUE_H
#define RTL9617C_PON_QUEUE_H

#include "rtl9617c-qm-deepq.h"

/* RTL8277C dal_ponmac initialization + stock TYFZA1 aal_puc_voq_bp_remap:
 * PUC0..127 feedback targets NI queues64..191; upper control queues have no
 * deep-Q feedback. Preserve the DATA register's unrelated upper 24 bits. */
static inline int rtl_pon_queue_backpressure(const struct rtl_dq_io *io)
{
	unsigned int queue;
	int ret;

	for (queue = 0; queue < 256; queue++) {
		ret = io->indirect(io->context, 0x80e8, 0x80000000U | queue);
		if (ret)
			return ret;
		io->write(io->context, 0x80ec,
			(io->read(io->context, 0x80ec) & ~255U) | (queue < 128 ? queue + 64 : 255));
		ret = io->indirect(io->context, 0x80e8, 0xc0000000U | queue);
		if (ret)
			return ret;
	}
	return 0;
}
#endif
