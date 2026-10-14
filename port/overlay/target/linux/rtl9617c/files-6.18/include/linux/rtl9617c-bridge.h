/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_RTL9617C_BRIDGE_H
#define _LINUX_RTL9617C_BRIDGE_H

#include <linux/types.h>

struct rtl9617c_bridge_tag {
	u16 tci, protocol;
};

/* Captured before the bridge applies its ingress PVID or removes a tag. */
struct rtl9617c_bridge_frame {
	u32 ingress, port, epoch;
	u64 pon_generation;
	u16 pon_gem;
	u16 protocol, session, vid, ppp_protocol;
	u8 source[6], destination[6], tags;
	struct rtl9617c_bridge_tag tag[2];
};

#endif
