/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_WIFI_POLICY_H
#define RTL9617C_WIFI_POLICY_H
#include <linux/types.h>
struct ieee80211_vif;
struct net_device;

/* Compiled by mac80211 under its wiphy lock through the actual Ethernet
 * classifier and WME downgrade path. Ordinary priorities0..7 use DSCP;
 * only256..263 override it. Inline VLAN frames use tagged_tid unless their
 * skb priority is an override. No metadata VLAN remains on native PE TX. */
struct rtl9617c_wifi_policy {
	unsigned char dscp_tid[64];
	unsigned char priority_tid[8];
	unsigned char tagged_tid;
	unsigned char noack_map;
	unsigned char qos;
};

int ieee80211_rtl9617c_mcast_policy(struct ieee80211_vif *vif,const u8 *address,
	struct rtl9617c_wifi_policy *policy);
int rtl9617c_wifi_mcast_policy(struct net_device *netdev,const u8 *address,
	unsigned int destination,unsigned int macid,
	const struct rtl9617c_wifi_policy *policy,u64 generation);

#endif
