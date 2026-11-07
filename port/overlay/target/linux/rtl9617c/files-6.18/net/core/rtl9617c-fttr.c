// SPDX-License-Identifier: GPL-2.0-only
/* FTTR software packet lifetime, independent of FE ownership and skb marks. */
#include <linux/atomic.h>
#include <linux/export.h>
#include <linux/errno.h>
#include <linux/if_vlan.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/rtl9617c-fttr.h>

static atomic64_t rtl_fttr_generation = ATOMIC64_INIT(1);

u64 rtl9617c_fttr_generation_read(void)
{
	return atomic64_read_acquire(&rtl_fttr_generation);
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_generation_read);

int rtl9617c_fttr_generation_set(bool active)
{
	u64 generation;

	ASSERT_RTNL();
	generation = rtl9617c_fttr_generation_read();
	if ((generation & 1) == !active)
		return 0;
	/* Do not make old skb identities valid again after counter exhaustion. */
	if (generation == U64_MAX)
		return -EOVERFLOW;
	atomic64_set_release(&rtl_fttr_generation, generation + 1);
	return 0;
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_generation_set);

bool rtl9617c_fttr_stamp(struct sk_buff *skb)
{
	if (!skb->rtl9617c_fttr_generation)
		skb->rtl9617c_fttr_generation = rtl9617c_fttr_generation_read();
	return !rtl9617c_fttr_stale(skb);
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_stamp);

bool rtl9617c_fttr_enqueue(struct sk_buff *skb, struct net_device *dev)
{
	bool tagged = skb_vlan_tag_present(skb);
	u8 endpoint;
	__be16 protocol = skb->protocol;

	while (is_vlan_dev(dev)) {
		tagged = true;
		dev = vlan_dev_real_dev(dev);
	}
	endpoint = READ_ONCE(dev->rtl9617c_fttr_egress);
	if (!endpoint)
		return true;
	/* AF_PACKET can declare ETH_P_ALL while supplying a VLAN on the wire.
	 * Inspect a present Ethernet header at enqueue; routed/NFQUEUE packets
	 * without it retain their IP protocol and cannot appear as management. */
	if (skb_mac_header_was_set(skb) && skb_mac_header(skb) == skb->data) {
		struct ethhdr storage;
		const struct ethhdr *eth = skb_header_pointer(skb, 0, sizeof(storage), &storage);
		if (!eth) return false;
		protocol = eth->h_proto;
	}
	if (!tagged && endpoint == 1 && READ_ONCE(dev->rtl9617c_fttr_omci) &&
	    protocol == htons(READ_ONCE(dev->rtl9617c_fttr_omci)))
		return true;
	return rtl9617c_fttr_stamp(skb);
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_enqueue);
