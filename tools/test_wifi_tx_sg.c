// SPDX-License-Identifier: GPL-2.0-only
/* Exercise real software TCP segmentation; no packet reaches a live device. */
#include <linux/etherdevice.h>
#include <linux/module.h>
#include <linux/tcp.h>
#include <net/gso.h>
#include <net/ip.h>
#include <net/tcp.h>
#include "wifi-tx-features-under-test.h"

#define PAYLOAD_BYTES 8192
#define MSS 1400

static u8 value(unsigned int offset)
{
	return (offset * 17 + (offset >> 8)) & 255;
}

static int check_segments(struct sk_buff *segments, bool scatter)
{
	struct sk_buff *skb;
	unsigned int offset = 0, count = 0;
	u8 payload[MSS];

	for (skb = segments; skb; skb = skb->next) {
		const struct iphdr *ip = ip_hdr(skb);
		const struct tcphdr *tcp = tcp_hdr(skb);
		unsigned int bytes = min_t(unsigned int, MSS, PAYLOAD_BYTES - offset);
		unsigned int i;

		if (++count > DIV_ROUND_UP(PAYLOAD_BYTES, MSS) ||
		    skb_is_nonlinear(skb) != scatter ||
		    skb->len != ETH_HLEN + sizeof(*ip) + sizeof(*tcp) + bytes ||
		    ntohl(tcp->seq) != 1000 + offset ||
		    ntohs(ip->tot_len) != sizeof(*ip) + sizeof(*tcp) + bytes ||
		    ip_fast_csum(ip, ip->ihl) ||
		    tcp_v4_check(sizeof(*tcp) + bytes, ip->saddr, ip->daddr,
			 skb_checksum(skb, skb_transport_offset(skb), sizeof(*tcp) + bytes, 0)) ||
		    skb_copy_bits(skb, skb_transport_offset(skb) + sizeof(*tcp), payload, bytes))
			return -EINVAL;
		for (i = 0; i < bytes; i++)
			if (payload[i] != value(offset + i))
				return -EBADMSG;
		offset += bytes;
	}
	return offset == PAYLOAD_BYTES && count == DIV_ROUND_UP(PAYLOAD_BYTES, MSS) ? 0 : -EINVAL;
}

static int run_case(struct net_device *dev, unsigned int linear, netdev_features_t features)
{
	struct sk_buff *skb, *segments;
	struct ethhdr *eth;
	struct iphdr *ip;
	struct tcphdr *tcp;
	unsigned int offset, frag = 0;
	u8 *data;
	int ret;

	skb = alloc_skb(256 + linear, GFP_KERNEL);
	if (!skb)
		return -ENOMEM;
	skb_reserve(skb, 64);
	skb_reset_mac_header(skb);
	eth = skb_put_zero(skb, ETH_HLEN);
	eth->h_proto = htons(ETH_P_IP);
	skb_set_network_header(skb, ETH_HLEN);
	ip = skb_put_zero(skb, sizeof(*ip));
	ip->version = 4;
	ip->ihl = 5;
	ip->ttl = 64;
	ip->protocol = IPPROTO_TCP;
	ip->saddr = htonl(0xc6126301);
	ip->daddr = htonl(0xc6126302);
	ip->tot_len = htons(sizeof(*ip) + sizeof(*tcp) + PAYLOAD_BYTES);
	ip->frag_off = htons(IP_DF);
	ip_send_check(ip);
	skb_set_transport_header(skb, ETH_HLEN + sizeof(*ip));
	tcp = skb_put_zero(skb, sizeof(*tcp));
	tcp->source = htons(40000);
	tcp->dest = htons(5209);
	tcp->seq = htonl(1000);
	tcp->ack = 1;
	tcp->doff = 5;
	tcp->check = ~tcp_v4_check(sizeof(*tcp) + PAYLOAD_BYTES, ip->saddr, ip->daddr, 0);
	data = skb_put(skb, linear);
	for (offset = 0; offset < linear; offset++)
		data[offset] = value(offset);
	while (offset < PAYLOAD_BYTES) {
		struct page *page = alloc_page(GFP_KERNEL);
		unsigned int bytes = min_t(unsigned int, PAGE_SIZE, PAYLOAD_BYTES - offset), i;

		if (!page) {
			kfree_skb(skb);
			return -ENOMEM;
		}
		data = page_address(page);
		for (i = 0; i < bytes; i++)
			data[i] = value(offset + i);
		skb_add_rx_frag(skb, frag++, page, 0, bytes, PAGE_SIZE);
		offset += bytes;
	}
	skb->dev = dev;
	skb->protocol = htons(ETH_P_IP);
	skb->ip_summed = CHECKSUM_PARTIAL;
	skb->csum_start = skb_transport_header(skb) - skb->head;
	skb->csum_offset = offsetof(struct tcphdr, check);
	skb_shinfo(skb)->gso_size = MSS;
	skb_shinfo(skb)->gso_segs = DIV_ROUND_UP(PAYLOAD_BYTES, MSS);
	skb_shinfo(skb)->gso_type = SKB_GSO_TCPV4;
	local_bh_disable();
	segments = skb_gso_segment(skb, features);
	local_bh_enable();
	if (IS_ERR_OR_NULL(segments)) {
		ret = segments ? PTR_ERR(segments) : -EINVAL;
	} else {
		ret = check_segments(segments, features & NETIF_F_SG);
		kfree_skb_list(segments);
	}
	kfree_skb(skb);
	return ret;
}

static int __init run_test(void)
{
	struct net_device *dev = alloc_etherdev(0);
	unsigned int linear;
	int ret = -ENOMEM;

	if (!dev)
		return ret;
	for (linear = 0; linear <= 64; linear += 64) {
		ret = run_case(dev, linear, 0);
		if (ret)
			break;
		ret = run_case(dev, linear, WIFI_TX_FEATURES);
		if (ret)
			break;
	}
	free_netdev(dev);
	if (!ret && !(WIFI_TX_FEATURES & NETIF_F_SG))
		ret = -EOPNOTSUPP;
	pr_info("wifi_tx_sg_test: result=%d advertised_sg=%d checksum_and_payload_cases=%u\n",
		ret, !!(WIFI_TX_FEATURES & NETIF_F_SG), linear / 64 * 2);
	return ret;
}

static void __exit finish_test(void) {}
module_init(run_test);
module_exit(finish_test);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Isolated Wi-Fi SG advertisement, TCP segment payload and checksum regression");
