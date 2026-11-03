// SPDX-License-Identifier: GPL-2.0-only
/* Isolated GRO/GSO regression. No packet can leave the test RX handler. */
#include <linux/etherdevice.h>
#include <linux/module.h>
#include <linux/rtnetlink.h>
#include <net/gro.h>
#include <net/gso.h>
#include <net/ip.h>
#include <net/udp.h>

#define COUNT 4
#define PAYLOAD 256

enum transform { NONE, CLONE, NAT, DODGY, LINEARIZE };
struct test_case {
	const char *name;
	bool random, short_last, bad_checksum, fragment, forged_ids;
	enum transform transform;
	unsigned int expected_batches, expected_id_changes;
};

static const struct test_case cases[] = {
	{ "sequential", .expected_batches = 1 },
	{ "sequential-short", .short_last = true, .expected_batches = 1 },
	{ "random", .random = true, .expected_batches = 4 },
	{ "random-short", .random = true, .short_last = true, .expected_batches = 4 },
	{ "sequential-clone", .transform = CLONE, .expected_batches = 1 },
	{ "sequential-nat", .transform = NAT, .expected_batches = 1 },
	{ "sequential-dodgy", .transform = DODGY, .expected_batches = 1 },
	{ "sequential-linearize", .transform = LINEARIZE, .expected_batches = 1 },
	{ "bad-checksum", .bad_checksum = true, .expected_batches = 4 },
	{ "fragment", .fragment = true, .expected_batches = 4 },
	/* Model accepting arbitrary IDs by changing only the retained IP headers
	 * after real GRO. Expected corruption demonstrates why that is unsafe.
	 */
	{ "unsafe-random-ids", .forged_ids = true,
	  .expected_batches = 1, .expected_id_changes = 3 },
	{ "unsafe-random-clone", .forged_ids = true, .transform = CLONE,
	  .expected_batches = 1, .expected_id_changes = 3 },
	{ "unsafe-random-nat", .forged_ids = true, .transform = NAT,
	  .expected_batches = 1, .expected_id_changes = 3 },
	{ "unsafe-random-dodgy", .forged_ids = true, .transform = DODGY,
	  .expected_batches = 1, .expected_id_changes = 3 },
	{ "unsafe-random-linearize", .forged_ids = true, .transform = LINEARIZE,
	  .expected_batches = 1, .expected_id_changes = 3 },
};

static const u16 ids[COUNT] = { 100, 43123, 9, 65501 };
static struct net_device *device;
static struct napi_struct napi;
static const struct test_case *active;
static unsigned int batches, datagrams, id_changes, failures;

static unsigned int payload_size(unsigned int index)
{
	return active->short_last && index == COUNT - 1 ? 71 : PAYLOAD;
}

static u8 value(unsigned int index, unsigned int offset)
{
	return index * 37 + offset * 17;
}

static struct sk_buff *packet(unsigned int index)
{
	unsigned int bytes = payload_size(index), i;
	struct sk_buff *skb = alloc_skb(512, GFP_KERNEL);
	struct ethhdr *eth;
	struct iphdr *ip;
	struct udphdr *udp;
	u8 *data;

	if (!skb)
		return NULL;
	skb_reserve(skb, 64 + NET_IP_ALIGN);
	eth = skb_put_zero(skb, ETH_HLEN);
	ether_addr_copy(eth->h_dest, device->dev_addr);
	eth->h_source[0] = 2;
	eth->h_source[5] = 2;
	eth->h_proto = htons(ETH_P_IP);
	ip = skb_put_zero(skb, sizeof(*ip));
	ip->version = 4;
	ip->ihl = 5;
	ip->ttl = 64;
	ip->protocol = IPPROTO_UDP;
	ip->tot_len = htons(sizeof(*ip) + sizeof(*udp) + bytes);
	ip->id = htons(active->random ? ids[index] : 100 + index);
	ip->frag_off = active->fragment ? htons(IP_MF) : 0;
	ip->saddr = htonl(0xc6126301);
	ip->daddr = htonl(0xc6126302);
	ip_send_check(ip);
	udp = skb_put_zero(skb, sizeof(*udp));
	udp->source = htons(45000);
	udp->dest = htons(5209);
	udp->len = htons(sizeof(*udp) + bytes);
	data = skb_put(skb, bytes);
	for (i = 0; i < bytes; i++)
		data[i] = value(index, i);
	udp->check = csum_tcpudp_magic(ip->saddr, ip->daddr,
			ntohs(udp->len), IPPROTO_UDP,
			csum_partial(udp, ntohs(udp->len), 0));
	if (!udp->check)
		udp->check = CSUM_MANGLED_0;
	if (active->bad_checksum)
		data[0] ^= 1;
	skb->ip_summed = CHECKSUM_NONE;
	skb->protocol = eth_type_trans(skb, device);
	return skb;
}

static void check_datagram(struct sk_buff *skb)
{
	unsigned int index = datagrams++, bytes, i, offset;
	struct iphdr *ip = ip_hdr(skb);
	struct udphdr *udp = udp_hdr(skb);
	u8 data[PAYLOAD];
	u16 expected_id;
	__sum16 checksum;

	if (index >= COUNT) {
		failures++;
		return;
	}
	bytes = payload_size(index);
	offset = skb_transport_offset(skb);
	expected_id = active->random || active->forged_ids ?
		ids[index] : 100 + index;
	id_changes += ntohs(ip->id) != expected_id;
	checksum = csum_tcpudp_magic(ip->saddr, ip->daddr,
			sizeof(*udp) + bytes, IPPROTO_UDP,
			skb_checksum(skb, offset, sizeof(*udp) + bytes, 0));
	if (skb->len != ETH_HLEN + sizeof(*ip) + sizeof(*udp) + bytes ||
	    skb_is_gso(skb) || ip_fast_csum(ip, ip->ihl) ||
	    ntohs(ip->tot_len) != sizeof(*ip) + sizeof(*udp) + bytes ||
	    ntohs(udp->len) != sizeof(*udp) + bytes ||
	    ip->ttl != 64 || ip->tos ||
	    ip->frag_off != (active->fragment ? htons(IP_MF) : 0) ||
	    ip->saddr != htonl(active->transform == NAT ? 0xc6126401 : 0xc6126301) ||
	    ip->daddr != htonl(active->transform == NAT ? 0xc6126402 : 0xc6126302) ||
	    udp->source != htons(active->transform == NAT ? 45001 : 45000) ||
	    udp->dest != htons(active->transform == NAT ? 5210 : 5209) ||
	    (!checksum) == active->bad_checksum ||
	    skb_copy_bits(skb, offset + sizeof(*udp), data, bytes)) {
		failures++;
		return;
	}
	for (i = 0; i < bytes; i++)
		if (data[i] != (value(index, i) ^ (active->bad_checksum && !i))) {
			failures++;
			break;
		}
}

static void translate(struct sk_buff *skb)
{
	struct iphdr *ip = ip_hdr(skb);
	struct udphdr *udp = udp_hdr(skb);
	__be32 src = htonl(0xc6126401), dst = htonl(0xc6126402);
	__be16 sport = htons(45001), dport = htons(5210);

	inet_proto_csum_replace4(&udp->check, skb, ip->saddr, src, true);
	inet_proto_csum_replace4(&udp->check, skb, ip->daddr, dst, true);
	inet_proto_csum_replace2(&udp->check, skb, udp->source, sport, false);
	inet_proto_csum_replace2(&udp->check, skb, udp->dest, dport, false);
	ip->saddr = src;
	ip->daddr = dst;
	udp->source = sport;
	udp->dest = dport;
	if (!udp->check)
		udp->check = CSUM_MANGLED_0;
	ip_send_check(ip);
}

static rx_handler_result_t receive(struct sk_buff **pskb)
{
	struct sk_buff *skb = *pskb, *segments, *seg, *clone = NULL;
	unsigned int index = 1;

	batches++;
	if (!skb_is_gso(skb)) {
		skb_set_transport_header(skb, sizeof(struct iphdr));
		skb_push(skb, ETH_HLEN);
		check_datagram(skb);
		goto out;
	}
	if (!(skb_shinfo(skb)->gso_type & SKB_GSO_FRAGLIST) ||
	    skb_shinfo(skb)->gso_segs != COUNT) {
		failures++;
		goto out;
	}
	if (active->forged_ids) {
		skb_walk_frags(skb, seg) {
			struct iphdr *ip = ip_hdr(seg);
			if (index >= COUNT) {
				failures++;
				goto out;
			}
			ip->id = htons(ids[index++]);
			ip_send_check(ip);
		}
	}
	if (active->transform == NAT)
		translate(skb);
	if (active->transform == DODGY)
		skb_shinfo(skb)->gso_type |= SKB_GSO_DODGY;
	if (active->transform == LINEARIZE && skb_linearize(skb)) {
		failures++;
		goto out;
	}
	if (active->transform == CLONE) {
		clone = skb_clone(skb, GFP_ATOMIC);
		if (!clone) {
			failures++;
			goto out;
		}
	}
	skb_push(skb, ETH_HLEN);
	segments = __skb_gso_segment(skb, NETIF_F_SG, false);
	if (IS_ERR_OR_NULL(segments)) {
		pr_err("udp_gro_semantics: %s segmentation=%ld\n", active->name,
			segments ? PTR_ERR(segments) : 0L);
		failures++;
	} else {
		for (seg = segments; seg; seg = seg->next)
			check_datagram(seg);
		kfree_skb_list(segments);
	}
out:
	kfree_skb(clone);
	kfree_skb(skb);
	return RX_HANDLER_CONSUMED;
}

static int unused_poll(struct napi_struct *n, int budget) { return 0; }
static netdev_tx_t discard(struct sk_buff *skb, struct net_device *dev)
{
	kfree_skb(skb);
	return NETDEV_TX_OK;
}
static const struct net_device_ops ops = { .ndo_start_xmit = discard };

static int __init run_test(void)
{
	unsigned int c, i, total_failures = 0;
	int ret;

	device = alloc_etherdev(0);
	if (!device)
		return -ENOMEM;
	strscpy(device->name, "tyfudp%d", IFNAMSIZ);
	device->netdev_ops = &ops;
	device->features |= NETIF_F_GRO | NETIF_F_GRO_FRAGLIST;
	eth_hw_addr_random(device);
	rtnl_lock();
	ret = register_netdevice(device);
	if (ret)
		goto unlock;
	ret = netdev_rx_handler_register(device, receive, NULL);
	if (ret)
		goto unregister;
	ret = dev_open(device, NULL);
	if (ret)
		goto handler;
	netif_napi_add(device, &napi, unused_poll);
	napi_enable(&napi);
	napi_disable(&napi);
	for (c = 0; c < ARRAY_SIZE(cases); c++) {
		struct sk_buff *packets[COUNT] = {};

		active = &cases[c];
		batches = datagrams = id_changes = failures = 0;
		for (i = 0; i < COUNT; i++) {
			packets[i] = packet(i);
			if (!packets[i])
				break;
		}
		if (i != COUNT) {
			while (i)
				kfree_skb(packets[--i]);
			total_failures++;
			break;
		}
		local_bh_disable();
		for (i = 0; i < COUNT; i++)
			napi_gro_receive(&napi, packets[i]);
		napi_gro_flush(&napi, false);
		netif_receive_skb_list(&napi.gro.rx_list);
		napi.gro.rx_count = 0;
		local_bh_enable();
		failures += batches != active->expected_batches || datagrams != COUNT ||
			id_changes != active->expected_id_changes;
		pr_info("udp_gro_semantics: %s batches=%u datagrams=%u id_changes=%u expected_changes=%u failures=%u\n",
			active->name, batches, datagrams, id_changes,
			active->expected_id_changes, failures);
		total_failures += failures;
	}
	netif_napi_del(&napi);
	ret = total_failures ? -EINVAL : 0;
	pr_info("udp_gro_semantics: result=%d cases=%u\n", ret, c);
handler:
	netdev_rx_handler_unregister(device);
unregister:
	unregister_netdevice(device);
unlock:
	rtnl_unlock();
	free_netdev(device);
	return ret;
}

static void __exit finish_test(void) {}
module_init(run_test);
module_exit(finish_test);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Isolated UDP GRO/GSO datagram semantics and unsafe-ID model regression");
