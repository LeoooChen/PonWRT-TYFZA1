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
	u64 multicast_transaction;
	u64 multicast_epoch;
	u16 pon_gem;
	u16 protocol, session, vid, ppp_protocol;
	u32 ip_source[4], ip_destination[4];
	u8 ip_protocol;
	u16 source_port, destination_port;
	u8 source[6], destination[6], tags;
	struct rtl9617c_bridge_tag tag[2];
};

struct net_device;
struct sk_buff;

/* Invoked inside the bridge multicast writer's lock, before any change.
 * Callbacks must not sleep. The paired token pins one owner through an RCU
 * read section, including when no owner exists at begin. */
struct rtl9617c_bridge_mcast_ops {
	void *context;
	void (*change_begin)(void *context, struct net_device *bridge);
	void (*change_end)(void *context);
	u64 (*capture_begin)(void *context, struct sk_buff *skb, bool local, u64 transaction);
	void (*capture_select)(void *context, struct sk_buff *skb, struct net_device *output);
	void (*capture_output)(void *context, struct sk_buff *skb);
	void (*capture_end)(void *context, u64 transaction, bool complete);
};
int rtl9617c_bridge_mcast_register(const struct rtl9617c_bridge_mcast_ops *ops);
void rtl9617c_bridge_mcast_unregister(const struct rtl9617c_bridge_mcast_ops *ops);
const struct rtl9617c_bridge_mcast_ops *rtl9617c_bridge_mcast_begin(struct net_device *bridge);
void rtl9617c_bridge_mcast_end(const struct rtl9617c_bridge_mcast_ops *ops);
u64 rtl9617c_bridge_mcast_capture(struct sk_buff *skb, bool local);
void rtl9617c_bridge_mcast_select(struct sk_buff *skb, struct net_device *output);
void rtl9617c_bridge_mcast_output(struct sk_buff *skb);
void rtl9617c_bridge_mcast_complete(u64 transaction, bool complete);

#endif
