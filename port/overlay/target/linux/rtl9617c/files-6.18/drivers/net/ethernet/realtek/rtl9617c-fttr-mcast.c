// SPDX-License-Identifier: GPL-2.0-only
/* Original onu_setup.sh .mc transport: shared GEM4095 and report admission.
 * This upper device uses the same P6 CPU/DMA gate as unicast services. It is
 * deliberately not a single-destination FE port: multicast needs replication.
 */
#include <linux/etherdevice.h>
#include <linux/if_bridge.h>
#include <linux/if_vlan.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/rtnetlink.h>
#include <linux/rtl9617c-bridge.h>
#include <linux/unaligned.h>
#include <net/ip.h>
#include <net/ipv6.h>
#include <net/netdev_lock.h>
#include <net/sch_generic.h>

#include "rtl9617c-fttr-mcast.h"

struct rtl_fttr_mcast {
	struct net_device *parent;
	bool admitted;
};

bool rtl_fttr_mcast_ready(const struct net_device *dev)
{
	const struct rtl_fttr_mcast *mc;

	if (!dev || !netif_running(dev) || !netif_carrier_ok(dev))
		return false;
	mc = netdev_priv(dev);
	return READ_ONCE(mc->admitted) && netif_running(mc->parent) && netif_carrier_ok(mc->parent);
}

/* Packets here still include their Ethernet header. Inspect the outer GEM
 * independently of customer tags. Native service VLANs are created by fttrd.
 */
static struct net_device *mcast_service(struct net_device *dev, __be16 protocol, u16 vid)
{
	struct rtl_fttr_mcast *mc = netdev_priv(dev);
	struct net_device *service, *bridge;

	if (protocol != htons(ETH_P_8021Q))
		return NULL;
	service = __vlan_find_dev_deep_rcu(mc->parent, protocol, vid);
	if (!service || service->group != 0x46544d55 || !netif_running(service) ||
	    br_port_get_stp_state(service) != BR_STATE_FORWARDING)
		return NULL;
	bridge = netdev_master_upper_dev_get_rcu(dev);
	return bridge && netif_is_bridge_master(bridge) && netif_running(bridge) &&
		netdev_master_upper_dev_get_rcu(service) == bridge ? service : NULL;
}

static bool mcast_outer(const struct sk_buff *skb, __be16 *protocol, u16 *vid, unsigned int *offset)
{
	u8 storage[18];
	const u8 *p = skb_header_pointer(skb, 0, ETH_HLEN, storage);

	if (!p)
		return false;
	*offset = ETH_HLEN;
	if (skb_vlan_tag_present(skb)) {
		*protocol = skb->vlan_proto;
		*vid = skb_vlan_tag_get_id(skb);
		return true;
	}
	*protocol = htons(get_unaligned_be16(p + 12));
	if (!eth_type_vlan(*protocol))
		return false;
	p = skb_header_pointer(skb, 0, sizeof(storage), storage);
	if (!p)
		return false;
	*offset += VLAN_HLEN;
	*vid = get_unaligned_be16(p + 14) & VLAN_VID_MASK;
	return true;
}

/* Original script redirects all IPv4 protocol2 and ICMPv6 types131/143.
 * IPv6 reports commonly carry a Hop-by-Hop Router Alert. Bound all reads by
 * the declared IP length; fragments without the transport header stay on UNI.
 */
static bool mcast_report(const struct sk_buff *skb, unsigned int offset)
{
	u8 storage[40];
	const u8 *p;
	unsigned int type, limit, length, next, i;

	p = skb_header_pointer(skb, offset - 2, 2, storage);
	if (!p)
		return false;
	type = get_unaligned_be16(p);
	for (i = 0; type == ETH_P_8021Q || type == ETH_P_8021AD; i++) {
		if (i == 2)
			return false;
		p = skb_header_pointer(skb, offset, VLAN_HLEN, storage);
		if (!p)
			return false;
		type = get_unaligned_be16(p + 2);
		offset += VLAN_HLEN;
	}
	if (type == ETH_P_IP) {
		p = skb_header_pointer(skb, offset, 20, storage);
		if (!p || p[0] >> 4 != 4 || (p[0] & 15) < 5)
			return false;
		length = get_unaligned_be16(p + 2);
		return length >= (p[0] & 15) * 4U && length <= skb->len - offset &&
			!(get_unaligned_be16(p + 6) & IP_OFFSET) && p[9] == IPPROTO_IGMP;
	}
	if (type != ETH_P_IPV6)
		return false;
	p = skb_header_pointer(skb, offset, 40, storage);
	if (!p || p[0] >> 4 != 6)
		return false;
	limit = offset + 40 + get_unaligned_be16(p + 4);
	if (limit > skb->len)
		return false;
	next = p[6];
	offset += 40;
	for (i = 0; i < 8; i++) {
		if (next == IPPROTO_ICMPV6) {
			p = offset + 8 <= limit ? skb_header_pointer(skb, offset, 8, storage) : NULL;
			return p && (p[0] == 131 || p[0] == 143);
		}
		p = offset + 8 <= limit ? skb_header_pointer(skb, offset, 8, storage) : NULL;
		if (!p)
			return false;
		if (next == NEXTHDR_FRAGMENT) {
			if (get_unaligned_be16(p + 2) & 0xfff8)
				return false;
			length = 8;
		} else if (next == NEXTHDR_HOP || next == NEXTHDR_ROUTING || next == NEXTHDR_DEST) {
			length = (p[1] + 1U) * 8;
		} else if (next == NEXTHDR_AUTH) {
			length = (p[1] + 2U) * 4;
		} else {
			return false;
		}
		if (offset + length > limit)
			return false;
		next = p[0];
		offset += length;
	}
	return false;
}

bool rtl_fttr_mcast_receive(struct net_device *dev, struct napi_struct *napi, struct sk_buff *skb)
{
	struct rtl_fttr_mcast *mc;
	unsigned int offset;
	__be16 protocol;
	u16 vid;

	if (!dev || !netif_running(dev))
		return false;
	mc = netdev_priv(dev);
	if (!READ_ONCE(mc->admitted) || !mcast_outer(skb, &protocol, &vid, &offset) ||
	    !mcast_report(skb, offset))
		return false;
	/* The shared VID4095 is downstream-only. Reports arrive on a real ONU's
	 * unicast GEM and must be authorized by that live service's bridge.
	 */
	if (!mcast_service(dev, protocol, vid))
		return false;
	skb_reset_mac_header(skb);
	skb->protocol = eth_hdr(skb)->h_proto;
	if (skb_vlan_pop(skb)) {
		dev->stats.rx_dropped++;
		dev_kfree_skb_any(skb);
		return true;
	}
	dev->stats.rx_packets++;
	dev->stats.rx_bytes += skb->len;
	skb->protocol = eth_type_trans(skb, dev);
	napi_gro_receive(napi, skb);
	return true;
}

bool rtl_fttr_mcast_drop(struct net_device *dev, const struct sk_buff *skb)
{
	u8 storage[ETH_HLEN];
	const u8 *p;
	unsigned int offset;
	__be16 protocol;
	u16 vid;

	if (!mcast_outer(skb, &protocol, &vid, &offset))
		return false;
	if (protocol == htons(ETH_P_8021Q) && vid == 4095) {
		struct rtl_fttr_mcast *mc = dev ? netdev_priv(dev) : NULL;

		/* A queued .mc frame may reach the parent after its upper closed. */
		return !dev || !netif_running(dev) || !READ_ONCE(mc->admitted);
	}
	if (!dev || !netif_running(dev) || !mcast_service(dev, protocol, vid))
		return false;
	p = skb_header_pointer(skb, 0, sizeof(storage), storage);
	/* Match the original ext0.+ drops, preserving IEEE reserved multicast. */
	return p && (p[0] == 1 || p[0] == 0x33) &&
		memcmp(p, "\x01\x80\xc2", 3);
}

static netdev_tx_t mcast_xmit(struct sk_buff *skb, struct net_device *dev)
{
	struct rtl_fttr_mcast *mc = netdev_priv(dev);
	bool from_fttr = skb->rtl9617c_fttr_ingress;
	u8 storage[ETH_HLEN];
	const u8 *p;
	unsigned int length = skb->len;

	if (!READ_ONCE(mc->admitted) || !netif_running(mc->parent) ||
	    !netif_carrier_ok(mc->parent) ||
	    skb->len + VLAN_HLEN + (skb_vlan_tag_present(skb) ? VLAN_HLEN : 0) > mc->parent->mtu + ETH_HLEN)
		goto drop;
	p = skb_header_pointer(skb, 0, sizeof(storage), storage);
	if (!p || !is_multicast_ether_addr(p) || is_broadcast_ether_addr(p))
		goto drop;
	/* Original .mc accepts IEEE1905; other locally originated IEEE reserved
	 * multicast stays on the per-ONU path. Rewrite returning FTTR sources.
	 */
	if (!memcmp(p, "\x01\x80\xc2", 3) && memcmp(p, "\x01\x80\xc2\0\0\x13", ETH_ALEN) && !from_fttr)
		goto drop;
	if (skb_cow_head(skb, VLAN_HLEN) || !pskb_may_pull(skb, ETH_HLEN))
		goto drop;
	if (from_fttr)
		ether_addr_copy(skb->data + ETH_ALEN, dev->dev_addr);
	skb_reset_mac_header(skb);
	if (skb_vlan_push(skb, htons(ETH_P_8021Q), 4095))
		goto drop;
	skb->dev = mc->parent;
	dev->stats.tx_packets++;
	dev->stats.tx_bytes += length;
	dev_queue_xmit(skb);
	return NETDEV_TX_OK;
drop:
	dev->stats.tx_dropped++;
	dev_kfree_skb_any(skb);
	return NETDEV_TX_OK;
}

void rtl_fttr_mcast_link(struct net_device *dev, bool up)
{
	struct rtl_fttr_mcast *mc;

	if (!dev)
		return;
	mc = netdev_priv(dev);
	if (up && mc->admitted && netif_running(dev) && netif_running(mc->parent))
		netif_carrier_on(dev);
	else
		netif_carrier_off(dev);
}

void rtl_fttr_mcast_gate(struct net_device *dev, bool admitted)
{
	struct rtl_fttr_mcast *mc;
	const struct rtl9617c_bridge_mcast_ops *writer;

	ASSERT_RTNL();
	if (!dev)
		return;
	mc = netdev_priv(dev);
	/* Close learned P6 host copies before the CPU business gate's RX purge.
	 * Worker publication holds RTNL, so it cannot reopen during this change. */
	writer = rtl9617c_bridge_mcast_begin(dev);
	WRITE_ONCE(mc->admitted, admitted);
	rtl9617c_bridge_mcast_end(writer);
	if (!admitted && netif_running(dev)) {
		dev_deactivate(dev);
		dev_activate(dev);
	}
	rtl_fttr_mcast_link(dev, netif_carrier_ok(mc->parent));
}

static int mcast_open(struct net_device *dev)
{
	struct rtl_fttr_mcast *mc = netdev_priv(dev);

	if (!netif_running(mc->parent))
		return -ENETDOWN;
	netif_start_queue(dev);
	rtl_fttr_mcast_link(dev, netif_carrier_ok(mc->parent));
	return 0;
}

static int mcast_stop(struct net_device *dev)
{
	struct rtl_fttr_mcast *mc = netdev_priv(dev);

	netif_stop_queue(dev);
	netif_carrier_off(dev);
	/* netif_running is already clear. Retire shared-NAPI reports and parent
	 * qdisc packets before a later open can admit this upper device again.
	 */
	rtl_fttr_mcast_quiesce(mc->parent);
	return 0;
}

static int mcast_iflink(const struct net_device *dev)
{
	const struct rtl_fttr_mcast *mc = netdev_priv(dev);

	return mc->parent->ifindex;
}

static const struct net_device_ops mcast_ops = {
	.ndo_open = mcast_open, .ndo_stop = mcast_stop, .ndo_start_xmit = mcast_xmit,
	.ndo_get_iflink = mcast_iflink, .ndo_set_mac_address = eth_mac_addr,
	.ndo_validate_addr = eth_validate_addr,
};

struct net_device *rtl_fttr_mcast_create(struct net_device *parent)
{
	struct net_device *dev = alloc_etherdev(sizeof(struct rtl_fttr_mcast));
	struct rtl_fttr_mcast *mc;
	int ret;

	ASSERT_RTNL();
	if (!dev)
		return ERR_PTR(-ENOMEM);
	mc = netdev_priv(dev);
	mc->parent = parent;
	strscpy(dev->name, "fttr-mc", IFNAMSIZ);
	/* Identify the native reserved-GEM upper to the resident controller. */
	dev->group = 0x46544d43;
	dev->rtl9617c_fttr_egress = 2;
	dev->netdev_ops = &mcast_ops;
	dev->mtu = ETH_DATA_LEN;
	dev->max_mtu = parent->mtu - VLAN_HLEN;
	dev->netns_immutable = true;
	dev->needed_headroom = parent->needed_headroom + VLAN_HLEN;
	/* The upper submits to the parent while holding its own TX queue lock. */
	netdev_lockdep_set_classes(dev);
	SET_NETDEV_DEV(dev, parent->dev.parent);
	eth_hw_addr_set(dev, parent->dev_addr);
	netif_carrier_off(dev);
	ret = register_netdevice(dev);
	if (ret) {
		free_netdev(dev);
		return ERR_PTR(ret);
	}
	ret = netdev_upper_dev_link(parent, dev, NULL);
	if (ret) {
		dev->needs_free_netdev = true;
		unregister_netdevice(dev);
		return ERR_PTR(ret);
	}
	return dev;
}

void rtl_fttr_mcast_destroy(struct net_device *dev)
{
	struct rtl_fttr_mcast *mc;

	if (!dev)
		return;
	mc = netdev_priv(dev);
	rtnl_lock();
	netdev_upper_dev_unlink(mc->parent, dev);
	unregister_netdevice(dev);
	rtnl_unlock();
	free_netdev(dev);
}
