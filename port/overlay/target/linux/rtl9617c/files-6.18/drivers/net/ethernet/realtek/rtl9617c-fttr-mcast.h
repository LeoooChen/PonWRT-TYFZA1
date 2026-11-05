/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_MCAST_H
#define RTL9617C_FTTR_MCAST_H

struct net_device;
struct napi_struct;
struct sk_buff;

struct net_device *rtl_fttr_mcast_create(struct net_device *parent);
void rtl_fttr_mcast_destroy(struct net_device *dev);
void rtl_fttr_mcast_gate(struct net_device *dev, bool admitted);
void rtl_fttr_mcast_link(struct net_device *dev, bool up);
void rtl_fttr_mcast_quiesce(struct net_device *parent);
bool rtl_fttr_mcast_receive(struct net_device *dev, struct napi_struct *napi, struct sk_buff *skb);
bool rtl_fttr_mcast_drop(struct net_device *dev, const struct sk_buff *skb);

#endif
