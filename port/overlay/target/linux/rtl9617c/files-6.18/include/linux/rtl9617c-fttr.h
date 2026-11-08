/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_RTL9617C_FTTR_H
#define _LINUX_RTL9617C_FTTR_H

#include <linux/skbuff.h>
#include <linux/rtl9617c-fttr-pe.h>
#include <linux/rtl9617c-bridge.h>

struct device;
struct net_device;
/* Hold an already configured, closed P6 while loading its downstream FPGA.
 * The caller owns a device link/reference to NI for the complete operation. */
int rtl9617c_ni_fttr_load_begin(struct device *dev);
void rtl9617c_ni_fttr_load_end(struct device *dev, int error);

#ifdef CONFIG_ARCH_RTL9617C
/* Built-in state survives native driver unload; zero means unrelated skb.
 * Odd generations are closed. Writers are serialized by RTNL. */
u64 rtl9617c_fttr_generation_read(void);
int rtl9617c_fttr_generation_set(bool active);
/* CPU-origin radio requests retain one reference until RPQ/software-drop
 * completion or joined device teardown has released their DMA mapping. */
bool rtl9617c_fttr_tx_get(u64 generation);
void rtl9617c_fttr_tx_put(u64 generation);
int rtl9617c_fttr_tx_drain(void);
/* The two radios own coherent storage until PE has joined. Registration
 * serializes with generation changes without requiring RTNL at PCI probe. */
int rtl9617c_fttr_pe_register(struct rtl_fttr_pe_generation *mirror,
			    struct rtl_fttr_pe_drain *drain);
void rtl9617c_fttr_pe_unregister(struct rtl_fttr_pe_generation *mirror);
bool rtl9617c_fttr_stamp(struct sk_buff *skb);
bool rtl9617c_fttr_enqueue(struct sk_buff *skb, struct net_device *dev);
static inline bool rtl9617c_fttr_stale(const struct sk_buff *skb)
{
	u64 generation = skb->rtl9617c_fttr_generation;
	return (generation && ((generation & 1) || generation != rtl9617c_fttr_generation_read())) ||
		rtl9617c_bridge_mcast_stale(skb);
}
static inline bool rtl9617c_fttr_differs(const struct sk_buff *a, const struct sk_buff *b)
{
	return a->rtl9617c_fttr_generation != b->rtl9617c_fttr_generation ||
		rtl9617c_bridge_mcast_differs(a,b);
}
static inline void rtl9617c_fttr_copy(struct sk_buff *to, const struct sk_buff *from)
{
	to->rtl9617c_fttr_generation = from->rtl9617c_fttr_generation;
	to->rtl9617c_fttr_ingress = from->rtl9617c_fttr_ingress;
	rtl9617c_bridge_mcast_copy(to,from);
}
#else
static inline bool rtl9617c_fttr_stale(const struct sk_buff *skb) { return false; }
static inline bool rtl9617c_fttr_enqueue(struct sk_buff *skb, struct net_device *dev) { return true; }
static inline bool rtl9617c_fttr_differs(const struct sk_buff *a, const struct sk_buff *b) { return false; }
static inline void rtl9617c_fttr_copy(struct sk_buff *to, const struct sk_buff *from) { }
#endif
#endif
