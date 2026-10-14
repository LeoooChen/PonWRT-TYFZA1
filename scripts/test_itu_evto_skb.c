// SPDX-License-Identifier: GPL-2.0-only
/* Isolated kernel SKBs exercise the same VLAN helpers as the PON datapath. */
#include <linux/module.h>
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/unaligned.h>
#include "rtl9617c-pon-path.h"
#include "rtl9617c-pon-vlan-skb.h"

#define CHECK(c) do { if (!(c)) { pr_err("TYF_EVTO skb check failed line=%u\n", __LINE__); ret=-EINVAL; goto out; } } while (0)

static void row(u8 bytes[16], u32 outer, u32 inner, u32 treatment)
{
	put_unaligned_be32(outer, bytes); put_unaligned_be32(inner, bytes + 4);
	put_unaligned_be32(0x000f0000, bytes + 8); put_unaligned_be32(treatment, bytes + 12);
}

static struct sk_buff *packet(bool ipv6, bool complete)
{
	unsigned int transport = ETH_HLEN + (ipv6 ? 48 : 20), length = transport + 8 + 64;
	struct sk_buff *skb = alloc_skb(length + 128, GFP_KERNEL);
	struct udphdr *udp;
	u8 *data;
	if (!skb) return NULL;
	skb_reserve(skb, 64); data = skb_put_zero(skb, length);
	data[0] = data[6] = 2; data[5] = 1; data[11] = 2;
	put_unaligned_be16(ipv6 ? ETH_P_IPV6 : ETH_P_IP, data + 12);
	if (ipv6) {
		struct ipv6hdr *ip = (void *)(data + ETH_HLEN);
		data[14] = 0x6b; data[15] = 0x80; /* DSCP46, ECN0. */
		ip->nexthdr = NEXTHDR_HOP; ip->hop_limit = 64;
		ip->payload_len = htons(length - ETH_HLEN - sizeof(*ip));
		data[ETH_HLEN + 40] = IPPROTO_UDP; /* Eight-byte hop-by-hop header. */
	} else {
		struct iphdr *ip = (void *)(data + ETH_HLEN);
		ip->version = 4; ip->ihl = 5; ip->tos = 46 << 2;
		ip->protocol = IPPROTO_UDP; ip->ttl = 64; ip->tot_len = htons(length - ETH_HLEN);
	}
	udp = (void *)(data + transport);
	udp->source = htons(ipv6 ? 546 : 68); udp->dest = htons(ipv6 ? 547 : 67); udp->len = htons(72);
	for (unsigned int i = 0; i < 64; i++) data[transport + 8 + i] = i * 31 + 7;
	skb_reset_mac_header(skb); skb_set_network_header(skb, ETH_HLEN); skb_set_transport_header(skb, transport);
	skb->protocol = htons(ipv6 ? ETH_P_IPV6 : ETH_P_IP);
	skb->csum_start = skb_headroom(skb) + transport; skb->csum_offset = 6;
	skb->ip_summed = complete ? CHECKSUM_COMPLETE : CHECKSUM_PARTIAL;
	if (complete) skb->csum = skb_checksum(skb, 0, skb->len, 0);
	return skb;
}

static int exercise(const struct rtl_itu_paths *paths, bool ipv6, bool complete)
{
	struct sk_buff *skb = packet(ipv6, complete);
	struct rtl_evto_packet input, output;
	unsigned int tags, length, transport;
	int ret = 0;
	CHECK(skb);
	length = skb->len; transport = ETH_HLEN + (ipv6 ? 48 : 20);
	CHECK(!rtl_evto_skb_read(paths, skb, &input, &tags));
	CHECK(!input.count && input.dscp == 46 && input.criteria == (ipv6 ? 2 : 1));
	CHECK(rtl_itu_path_transmit_vlan(paths, &input, &output));
	CHECK(!rtl_evto_skb_write(skb, &input, &output, tags));
	CHECK(skb->len == length + 4 && get_unaligned_be16(skb->data + 14) == (5 << 13 | 200));
	if (!complete) CHECK(skb_checksum_start_offset(skb) == transport + 4 && skb->csum_offset == 6);
	if (complete) CHECK(csum_fold(skb->csum) == csum_fold(skb_checksum(skb, 0, skb->len, 0)));
	CHECK(!rtl_evto_skb_read(paths, skb, &input, &tags));
	CHECK(input.count == 1 && tags == 1 && input.dscp == 46 && input.criteria == (ipv6 ? 2 : 1));
	CHECK(rtl_itu_path_receive_vlan(paths, 8, &input, &output));
	CHECK(!rtl_evto_skb_write(skb, &input, &output, tags));
	CHECK(skb->len == length);
	if (!complete) CHECK(skb_checksum_start_offset(skb) == transport);
	if (complete) CHECK(csum_fold(skb->csum) == csum_fold(skb_checksum(skb, 0, skb->len, 0)));
	for (unsigned int i = 0; i < 64; i++) CHECK(skb->data[transport + 8 + i] == (u8)(i * 31 + 7));
out:
	kfree_skb(skb);
	return ret;
}

static int metadata(const struct rtl_itu_paths *paths)
{
	struct sk_buff *skb = packet(false, false);
	struct rtl_evto_packet input, output;
	unsigned int tags, length;
	int ret = 0;
	CHECK(skb);
	length = skb->len;
	__vlan_hwaccel_put_tag(skb, htons(ETH_P_8021Q), 5 << 13 | 200);
	CHECK(!rtl_evto_skb_read(paths, skb, &input, &tags) && input.count == 1 && tags == 0);
	CHECK(rtl_itu_path_receive_vlan(paths, 8, &input, &output));
	CHECK(!rtl_evto_skb_write(skb, &input, &output, tags) && !skb_vlan_tag_present(skb) && skb->len == length);
	__vlan_hwaccel_put_tag(skb, htons(ETH_P_8021Q), 5 << 13 | 300);
	CHECK(!rtl_evto_skb_read(paths, skb, &input, &tags));
	CHECK(!rtl_evto_skb_write(skb, &input, &input, tags));
	CHECK(!skb_vlan_tag_present(skb) && skb->len == length + 4 && get_unaligned_be16(skb->data + 14) == (5 << 13 | 300));
out:
	kfree_skb(skb);
	return ret;
}

static int __init evto_test(void)
{
	struct rtl_itu_paths *paths = kzalloc(sizeof(*paths) + 2 * sizeof(paths->profile[0]), GFP_KERNEL);
	int ret = -ENOMEM;
	if (!paths) return ret;
	paths->count = 1; paths->profile_count = 2;
	paths->path[0] = (struct rtl_itu_path){.gem_id = 4000, .vlan = 100, .pbits = 255, .downstream_pbits = 255,
		.index = 8, .tcont = 1, .vlan_profile = {0, RTL_EVTO_NONE, RTL_EVTO_NONE, RTL_EVTO_NONE, 1}};
	for (unsigned int i = 0; i < 2; i++) {
		paths->profile[i].input_tpid = paths->profile[i].output_tpid = ETH_P_8021Q;
		paths->profile[i].count = 1;
	}
	row(paths->profile[0].row[0], 0xf8000000, 0xf8000000, 5 << 16 | 100 << 3 | 4);
	row(paths->profile[1].row[0], 0xf8000000, 0x80004000 | 100 << 15, 8 << 16 | 200 << 3 | 4);
	put_unaligned_be32(0x400f0000, paths->profile[1].row[0] + 8);
	ret = exercise(paths, false, false);
	if (!ret) ret = exercise(paths, true, false);
	if (!ret) ret = exercise(paths, false, true);
	if (!ret) ret = metadata(paths);
	kfree(paths);
	if (!ret) pr_info("TYF_EVTO kernel SKBs passed: IPv4, IPv6 DHCP extension, exact payload, checksum offsets/complete sum, VLAN metadata\n");
	return ret;
}
static void __exit evto_done(void) {}
module_init(evto_test); module_exit(evto_done);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Isolated TYFZA1 EVTO kernel SKB verification");
