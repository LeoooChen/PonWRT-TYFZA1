// SPDX-License-Identifier: GPL-2.0-only
/* Isolated real-kernel test; the RX handler consumes every synthetic packet. */
#include <linux/module.h>
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/ip.h>
#include <linux/tcp.h>
#include <linux/rtnetlink.h>
#include <net/gro.h>
#include <net/ip.h>
#include "rtl9617c-ni.h"
#include "wifi-receive-under-test.h"

static struct net_device *devices[2];
static unsigned int received[2], segments[2], failures;
static struct napi_struct napi;

static rx_handler_result_t check_receive(struct sk_buff **pskb)
{
	struct sk_buff *skb = *pskb;
	unsigned int lane = skb->dev == devices[1];
	u8 payload[1024];
	unsigned int i;

	received[lane]++;
	segments[lane] += skb_shinfo(skb)->gso_segs;
	if (skb->dev != devices[lane] || !skb->rtl9617c_wifi_miss ||
	    skb->protocol != htons(ETH_P_IP) || skb->len != 1064 ||
	    skb_shinfo(skb)->gso_segs != 4 ||
	    skb_copy_bits(skb, 40, payload, sizeof(payload))) {
		failures++;
	} else {
		for (i = 0; i < sizeof(payload); i++)
			if (payload[i] != (u8)(i + lane * 37)) {
				failures++;
				break;
			}
	}
	consume_skb(skb);
	return RX_HANDLER_CONSUMED;
}

static struct sk_buff *packet(unsigned int lane, unsigned int index)
{
	struct sk_buff *skb = alloc_skb(512, GFP_KERNEL);
	struct ethhdr *eth;
	struct iphdr *ip;
	struct tcphdr *tcp;
	u8 *payload;
	unsigned int i;

	if (!skb)
		return NULL;
	skb_reserve(skb, NET_IP_ALIGN);
	eth = skb_put_zero(skb, ETH_HLEN);
	eth->h_dest[0] = eth->h_source[0] = 2;
	eth->h_dest[5] = 1;
	eth->h_source[5] = 2;
	eth->h_proto = htons(ETH_P_IP);
	ip = skb_put_zero(skb, sizeof(*ip));
	ip->version = 4;
	ip->ihl = 5;
	ip->ttl = 64;
	ip->protocol = IPPROTO_TCP;
	ip->tot_len = htons(296);
	ip->id = htons(index);
	ip->frag_off = htons(IP_DF);
	ip->saddr = htonl(0xc6126301);
	ip->daddr = htonl(0xc6126302);
	ip_send_check(ip);
	tcp = skb_put_zero(skb, sizeof(*tcp));
	tcp->source = htons(45000);
	tcp->dest = htons(5209);
	tcp->seq = htonl(1000 + index * 256);
	tcp->ack_seq = htonl(1);
	tcp->doff = 5;
	tcp->ack = 1;
	tcp->window = htons(65535);
	payload = skb_put(skb, 256);
	for (i = 0; i < 256; i++)
		payload[i] = i + lane * 37;
	skb->ip_summed = CHECKSUM_UNNECESSARY;
	skb->dev = devices[lane];
	return skb;
}

static int unused_poll(struct napi_struct *n, int budget)
{
	return 0;
}

static netdev_tx_t discard(struct sk_buff *skb, struct net_device *dev)
{
	consume_skb(skb);
	return NETDEV_TX_OK;
}

static const struct net_device_ops ops = { .ndo_start_xmit = discard };

static int __init run_test(void)
{
	struct sk_buff *packets[8] = {};
	unsigned int i, registered = 0, handlers = 0;
	int ret = -ENOMEM;

	rtnl_lock();
	for (i = 0; i < 2; i++) {
		devices[i] = alloc_etherdev(0);
		if (!devices[i]) {
			ret = -ENOMEM;
			goto out;
		}
		strscpy(devices[i]->name, "tyfgro%d", IFNAMSIZ);
		devices[i]->netdev_ops = &ops;
		devices[i]->features |= NETIF_F_GRO;
		eth_hw_addr_random(devices[i]);
		ret = register_netdevice(devices[i]);
		if (ret)
			goto out;
		registered++;
		ret = netdev_rx_handler_register(devices[i], check_receive, NULL);
		if (ret)
			goto out;
		handlers++;
		ret = dev_open(devices[i], NULL);
		if (ret)
			goto out;
	}
	for (i = 0; i < 8; i++) {
		packets[i] = packet(i & 1, i / 2);
		if (!packets[i]) {
			ret = -ENOMEM;
			goto out;
		}
	}
	netif_napi_add(devices[0], &napi, unused_poll);
	napi_enable(&napi);
	napi_disable(&napi);
	local_bh_disable();
	for (i = 0; i < 8; i++) {
		struct rtl9617c_ni_wifi_endpoint endpoint = { .netdev = devices[i & 1] };
		wifi_receive(&endpoint, &napi, packets[i], false);
		packets[i] = NULL;
	}
	napi_gro_flush(&napi, false);
	netif_receive_skb_list(&napi.gro.rx_list);
	napi.gro.rx_count = 0;
	local_bh_enable();
	netif_napi_del(&napi);
	ret = failures || received[0] != 1 || received[1] != 1 ||
		segments[0] != 4 || segments[1] != 4 ? -EINVAL : 0;
	pr_info("wifi_miss_gro_test: result=%d packets=%u/%u segments=%u/%u failures=%u\n",
		ret, received[0], received[1], segments[0], segments[1], failures);
out:
	for (i = 0; i < 8; i++)
		kfree_skb(packets[i]);
	for (i = 0; i < handlers; i++)
		netdev_rx_handler_unregister(devices[i]);
	for (i = 0; i < registered; i++)
		unregister_netdevice(devices[i]);
	rtnl_unlock();
	for (i = 0; i < 2; i++)
		if (devices[i])
			free_netdev(devices[i]);
	return ret;
}

static void __exit finish_test(void) {}
module_init(run_test);
module_exit(finish_test);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Isolated Wi-Fi FE-miss GRO payload and VIF regression");
