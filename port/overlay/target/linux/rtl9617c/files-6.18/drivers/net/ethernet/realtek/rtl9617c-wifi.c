// SPDX-License-Identifier: GPL-2.0-or-later
/* mac80211 Ethernet ingress and the shared NI/FE Wi-Fi identity lifetime. */
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/netfilter.h>
#include <linux/netfilter_netdev.h>
#include <linux/ppp_defs.h>
#include <linux/rtnetlink.h>
#include <linux/slab.h>
#include <linux/unaligned.h>
#include <net/cfg80211.h>
#include "rtl9617c-ni.h"
#include <linux/rtl9617c-wifi.h>
#include <linux/rtl9617c-wifi-policy.h>
#include <linux/rtl9617c-bridge.h>
#include <linux/rtl9617c-qm-layout.h>

struct rtl_wifi_port {
	struct list_head list;
	struct rtl9617c_ni_wifi_endpoint endpoint;
	struct nf_hook_ops hook;
	unsigned int id;
	bool hooked;
};
static LIST_HEAD(wifi_ports);
static struct device *wifi_ni;
static DEFINE_MUTEX(wifi_mcast_lock);

unsigned int rtl_wifi_ingress(void *private, struct sk_buff *skb,
				const struct nf_hook_state *state)
{
	struct rtl_wifi_port *port = private;
	struct sk_buff *copy;
	__be16 protocol = skb->protocol;
	unsigned int offset = 0;
	int ret;
	const struct rtl9617c_bridge_frame *host = skb_ext_find(skb, SKB_EXT_RTL9617C_BRIDGE);
	bool multicast = is_multicast_ether_addr(eth_hdr(skb)->h_dest);

	/* Other recipients already received their copies. Never query this
	 * host-only packet again; the owning bridge consumes its decision. */
	if (host && host->multicast_host_group) return NF_ACCEPT;
	/* A QM miss starts the same Linux ingress again with its original VIF.
	 * Clear only our provenance bit; all subsequent policy hooks still run. */
	if (skb->rtl9617c_wifi_miss) {
		skb->rtl9617c_wifi_miss = 0;
		return NF_ACCEPT;
	}
	if (!rtl9617c_ni_wifi_enabled(wifi_ni) || skb_is_gso(skb) ||
	    skb->ip_summed == CHECKSUM_PARTIAL || skb_mac_header_len(skb) != ETH_HLEN ||
	    is_broadcast_ether_addr(eth_hdr(skb)->h_dest)) return NF_ACCEPT;
	/* Multicast misses use the CPU RX pool. Keep oversized frames in
	 * Linux until that path can retain a complete multi-buffer VIF frame. */
	if (multicast && skb->len + ETH_HLEN + (skb_vlan_tag_present(skb) ? VLAN_HLEN : 0) > RTL_QM_DATA_BYTES - 16)
		return NF_ACCEPT;
	if (protocol == htons(ETH_P_PPP_SES)) {
		if (multicast) return NF_ACCEPT;
		u8 header[8], *pppoe = skb_header_pointer(skb, 0, sizeof(header), header);
		unsigned int ppp;

		if (!pppoe || pppoe[0] != 0x11 || pppoe[1]) return NF_ACCEPT;
		ppp = get_unaligned_be16(pppoe + 6);
		if (ppp != PPP_IP && ppp != PPP_IPV6) return NF_ACCEPT;
		protocol = htons(ppp == PPP_IP ? ETH_P_IP : ETH_P_IPV6);
		offset = sizeof(header);
	}
	if (protocol == htons(ETH_P_IP)) {
		struct iphdr header, *ip = skb_header_pointer(skb, offset, sizeof(header), &header);
		if (!ip || (ip->protocol != IPPROTO_TCP && ip->protocol != IPPROTO_UDP) ||
		    (multicast && ip->protocol != IPPROTO_UDP)) return NF_ACCEPT;
	} else if (protocol == htons(ETH_P_IPV6)) {
		struct ipv6hdr header, *ip = skb_header_pointer(skb, offset, sizeof(header), &header);
		if (!ip || (ip->nexthdr != IPPROTO_TCP && ip->nexthdr != IPPROTO_UDP) ||
		    (multicast && ip->nexthdr != IPPROTO_UDP)) return NF_ACCEPT;
	} else return NF_ACCEPT;
	if (multicast) {
		struct rtl9617c_bridge_frame *wire = skb_ext_add(skb, SKB_EXT_RTL9617C_BRIDGE);
		if (!wire) return NF_ACCEPT;
		memset(wire, 0, sizeof(*wire));
		wire->wifi_id = port->id;
	}
	/* eth_type_trans() already retained this header in the skb. Restore the
	 * data pointer for NI's copy without cloning an ordinary untagged frame. */
	if (!skb_vlan_tag_present(skb)) {
		skb_push(skb, ETH_HLEN);
		ret = rtl9617c_ni_wifi_lookup(wifi_ni, skb, port->id);
		if (!ret) return NF_STOLEN;
		skb_pull(skb, ETH_HLEN);
		return NF_ACCEPT;
	}
	copy = skb_copy_expand(skb, ETH_HLEN + VLAN_HLEN, 0, GFP_ATOMIC);
	if (!copy) return NF_ACCEPT;
	skb_push(copy, ETH_HLEN);
	memcpy(copy->data, skb_mac_header(skb), ETH_HLEN);
	skb_reset_mac_header(copy);
	if (skb_vlan_tag_present(copy)) {
		copy = __vlan_hwaccel_push_inside(copy);
		if (!copy) return NF_ACCEPT;
	}
	ret = rtl9617c_ni_wifi_lookup(wifi_ni, copy, port->id);
	if (ret) { kfree_skb(copy); return NF_ACCEPT; }
	consume_skb(skb);
	return NF_STOLEN;
}

static void wifi_receive(struct rtl9617c_ni_wifi_endpoint *endpoint,
			 struct napi_struct *napi, struct sk_buff *skb, bool forwarded)
{
	skb_reset_mac_header(skb);
	if (forwarded) {
		int depth = ETH_HLEN;
		skb->protocol = eth_hdr(skb)->h_proto;
		__vlan_get_protocol(skb, skb->protocol, &depth);
		skb_set_network_header(skb, depth);
		dev_queue_xmit(skb);
	} else {
		skb->protocol = eth_type_trans(skb, endpoint->netdev);
		skb->rtl9617c_wifi_miss = 1;
		if (is_multicast_ether_addr(eth_hdr(skb)->h_dest)) {
			struct rtl_wifi_port *port = container_of(endpoint, struct rtl_wifi_port, endpoint);
			struct rtl9617c_bridge_frame *wire = skb_ext_add(skb, SKB_EXT_RTL9617C_BRIDGE);
			if (wire) {
				memset(wire, 0, sizeof(*wire));
				wire->wifi_id = port->id;
			}
		}
		/* NI already owns a NAPI poll. Keep its batching on FE misses too;
		 * the original VIF and provenance survive GRO and ingress policy. */
		napi_gro_receive(napi, skb);
	}
}

static struct rtl_wifi_port *wifi_port(struct net_device *netdev)
{
	struct rtl_wifi_port *port;
	list_for_each_entry(port, &wifi_ports, list)
		if (port->endpoint.netdev == netdev) return port;
	return NULL;
}

static int wifi_del(struct rtl_wifi_port *port);

static int wifi_add(struct net_device *netdev)
{
	struct rtl_wifi_port *port;
	int ret;

	port = wifi_port(netdev);
	if (port) {
		if (port->hooked) return 0;
		/* A failed DOWN/UNREGISTER kept the old endpoint and FE state.
		 * Complete that withdrawal before publishing a new ingress hook. */
		ret = wifi_del(port);
		if (ret) return ret;
	}
	port = kzalloc(sizeof(*port), GFP_KERNEL);
	if (!port) return -ENOMEM;
	port->endpoint.netdev = netdev;
	port->endpoint.receive = wifi_receive;
	ret = rtl9617c_ni_wifi_attach(wifi_ni, &port->endpoint);
	if (ret < 0) { kfree(port); return ret; }
	port->id = ret;
	port->hook = (struct nf_hook_ops){.hook = rtl_wifi_ingress, .priv = port,
		.dev = netdev, .pf = NFPROTO_NETDEV, .hooknum = NF_NETDEV_INGRESS, .priority = INT_MIN};
	ret = nf_register_net_hook(dev_net(netdev), &port->hook);
	if (ret) {
		if (rtl9617c_ni_wifi_detach(wifi_ni, port->id)) {
			list_add_tail(&port->list, &wifi_ports);
			return ret;
		}
		rtl9617c_ni_fe_remove_port(wifi_ni, netdev);
		kfree(port);
		return ret;
	}
	port->hooked = true;
	list_add_tail(&port->list, &wifi_ports);
	netdev_info(netdev, "native NE Wi-Fi interface ID%u registered\n", port->id);
	return 0;
}

static int wifi_del(struct rtl_wifi_port *port)
{
	int ret;
	if (port->hooked) {
		nf_unregister_net_hook(dev_net(port->endpoint.netdev), &port->hook);
		port->hooked = false;
	}
	ret = rtl9617c_ni_wifi_detach(wifi_ni, port->id);
	if (ret) {
		netdev_err(port->endpoint.netdev, "NI queue drain failed: %d; interface ID%u retained\n", ret, port->id);
		return ret;
	}
	list_del(&port->list);
	kfree(port);
	return 0;
}

static int wifi_event(struct notifier_block *notifier, unsigned long event, void *ptr)
{
	struct net_device *netdev = netdev_notifier_info_to_dev(ptr);
	struct rtl_wifi_port *port;
	bool supported;
	int ret;

	if (!netdev->ieee80211_ptr)
		return NOTIFY_DONE;
	supported = netdev->ieee80211_ptr->iftype == NL80211_IFTYPE_AP ||
		netdev->ieee80211_ptr->iftype == NL80211_IFTYPE_STATION;
	/* A down vif can change type without unregistering. Retire its old NI
	 * ownership on removal, and before a mesh/monitor vif starts receiving. */
	if (event == NETDEV_DOWN || event == NETDEV_UNREGISTER ||
	    (event == NETDEV_UP && !supported)) {
		port = wifi_port(netdev);
		if (port) wifi_del(port);
		/* A retained NI endpoint needs its FE port if AP/STA is reopened. */
		if (event == NETDEV_UNREGISTER ||
		    (event == NETDEV_UP && !wifi_port(netdev)))
			rtl9617c_ni_fe_remove_port(wifi_ni, netdev);
	} else if (supported && (event == NETDEV_REGISTER || event == NETDEV_UP)) {
		ret = wifi_add(netdev);
		if (ret) netdev_err(netdev, "native NE Wi-Fi registration failed: %d\n", ret);
	}
	return NOTIFY_DONE;
}
/* Publish the FE identity before nftables binds a recreated AP. */
static struct notifier_block wifi_notifier = {.notifier_call = wifi_event, .priority = 1};

int rtl_wifi_start(struct device *ni)
{
	int ret;
	wifi_ni = ni;
	ret = register_netdevice_notifier(&wifi_notifier);
	if (ret) wifi_ni = NULL;
	return ret;
}

void rtl_wifi_stop(void)
{
	struct rtl_wifi_port *port, *next;
	unregister_netdevice_notifier(&wifi_notifier);
	rtnl_lock();
	list_for_each_entry_safe(port, next, &wifi_ports, list) wifi_del(port);
	mutex_lock(&wifi_mcast_lock);
	/* Join binding callers before clearing the supplier. Radio teardown may
	 * then free its table without a borrowed FE pointer surviving this stop. */
	rtl9617c_ni_wifi_mcast_detach(wifi_ni);
	wifi_ni = NULL;
	mutex_unlock(&wifi_mcast_lock);
	rtnl_unlock();
}

int rtl9617c_wifi_setup_tc(struct net_device *netdev, enum tc_setup_type type, void *data)
{
	if (!wifi_ni) return -ENODEV;
	return rtl9617c_ni_fe_setup_tc(wifi_ni, netdev, type, data);
}
EXPORT_SYMBOL_GPL(rtl9617c_wifi_setup_tc);

int rtl9617c_wifi_peer_route(struct net_device *netdev, const u8 *address, unsigned int destination)
{
	if (!wifi_ni) return -ENODEV;
	return rtl9617c_ni_wifi_peer_route(wifi_ni, netdev, address, destination);
}
EXPORT_SYMBOL_GPL(rtl9617c_wifi_peer_route);

int rtl9617c_wifi_mcast_table(struct net_device *netdev,unsigned int destination,
 struct rtl_mc_pe_table *table,bool bind)
{
	int ret;
	mutex_lock(&wifi_mcast_lock);
	ret = wifi_ni ? rtl9617c_ni_wifi_mcast_table(wifi_ni, netdev, destination, table, bind) : bind ? -ENODEV : 0;
	mutex_unlock(&wifi_mcast_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_wifi_mcast_table);

int rtl9617c_wifi_mcast_policy(struct net_device *netdev,const u8 *address,
 unsigned int destination,unsigned int macid,const struct rtl9617c_wifi_policy *policy,u64 generation)
{
	int ret;
	mutex_lock(&wifi_mcast_lock);
	ret = wifi_ni ? rtl9617c_ni_wifi_mcast_policy(wifi_ni,netdev,address,destination,macid,policy,generation) : -ENODEV;
	mutex_unlock(&wifi_mcast_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_wifi_mcast_policy);

int rtl9617c_wifi_id(struct net_device *netdev)
{
	if (!wifi_ni) return -ENODEV;
	return rtl9617c_ni_wifi_id(wifi_ni, netdev);
}
EXPORT_SYMBOL_GPL(rtl9617c_wifi_id);

int rtl9617c_wifi_retire(unsigned int pool)
{
	int ret;
	mutex_lock(&wifi_mcast_lock);
	ret = wifi_ni ? rtl9617c_ni_wifi_retire(wifi_ni, pool) : -ENODEV;
	mutex_unlock(&wifi_mcast_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_wifi_retire);

