/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_VLAN_SKB_H
#define RTL9617C_PON_VLAN_SKB_H
#include <linux/ip.h>
#include <linux/udp.h>
#include <linux/ppp_defs.h>
#include <net/ip.h>
#include <net/dsfield.h>
#include <net/ipv6.h>

static int rtl_evto_tpid(const struct rtl_itu_paths *paths, unsigned int type)
{
	if (type == ETH_P_8021Q || type == ETH_P_8021AD || type == 0x9100) return 1;
	for (unsigned int i = 0; i < paths->profile_count; i++)
		if (type == paths->profile[i].input_tpid || type == paths->profile[i].output_tpid) return 1;
	return 0;
}

static int rtl_evto_skb_read(const struct rtl_itu_paths *paths, struct sk_buff *skb,
	struct rtl_evto_packet *packet, unsigned int *wire_tags)
{
	unsigned int type, offset = ETH_HLEN, udp_offset = 0;
	struct udphdr udp_buffer;
	const struct udphdr *udp;
	*packet = (struct rtl_evto_packet){.priority = skb->priority & 7};
	*wire_tags = 0;
	if (!pskb_may_pull(skb, ETH_HLEN)) return -EINVAL;
	type = get_unaligned_be16(skb->data + 12);
	if (skb_vlan_tag_present(skb))
		packet->tag[packet->count++] = (struct rtl_evto_tag){ntohs(skb->vlan_proto), skb_vlan_tag_get(skb)};
	while (rtl_evto_tpid(paths, type)) {
		if (packet->count == RTL_EVTO_TAGS || !pskb_may_pull(skb, offset + VLAN_HLEN)) return -EINVAL;
		packet->tag[packet->count++] = (struct rtl_evto_tag){type, get_unaligned_be16(skb->data + offset)};
		type = get_unaligned_be16(skb->data + offset + 2);
		offset += VLAN_HLEN;
		(*wire_tags)++;
	}
	packet->protocol = type;
	if (packet->count) packet->priority = packet->tag[0].tci >> VLAN_PRIO_SHIFT;
	if (type == ETH_P_PPP_SES) {
		__be16 protocol, *ppp = skb_header_pointer(skb, offset + 6, sizeof(protocol), &protocol);
		if (ppp && (ntohs(*ppp) == PPP_IP || ntohs(*ppp) == PPP_IPV6)) {
			type = ntohs(*ppp) == PPP_IP ? ETH_P_IP : ETH_P_IPV6;
			offset += 8;
		}
	}
	if (type == ETH_P_IP) {
		struct iphdr buffer;
		const struct iphdr *ip = skb_header_pointer(skb, offset, sizeof(buffer), &buffer);
		if (ip && ip->version == 4 && ip->ihl >= 5) {
			packet->dscp = ip->tos >> 2;
			if (ip->protocol == IPPROTO_UDP && !(ntohs(ip->frag_off) & IP_OFFSET)) udp_offset = offset + ip->ihl * 4;
		}
	} else if (type == ETH_P_IPV6) {
		struct ipv6hdr buffer;
		const struct ipv6hdr *ip = skb_header_pointer(skb, offset, sizeof(buffer), &buffer);
		if (ip && ip->version == 6) {
			u8 next = ip->nexthdr;
			__be16 fragment;
			int position;
			packet->dscp = ipv6_get_dsfield(ip) >> 2;
			position = ipv6_skip_exthdr(skb, offset + sizeof(buffer), &next, &fragment);
			if (position >= 0 && next == IPPROTO_UDP && !(ntohs(fragment) & ~7U)) udp_offset = position;
		}
	}
	udp = udp_offset ? skb_header_pointer(skb, udp_offset, sizeof(udp_buffer), &udp_buffer) : NULL;
	if (udp) {
		unsigned int source = ntohs(udp->source), destination = ntohs(udp->dest);
		if (type == ETH_P_IP && (source == 67 || source == 68) && (destination == 67 || destination == 68)) packet->criteria = 1;
		if (type == ETH_P_IPV6 && (source == 546 || source == 547) && (destination == 546 || destination == 547)) packet->criteria = 2;
	}
	return 0;
}

static int rtl_evto_skb_write(struct sk_buff *skb, const struct rtl_evto_packet *before,
	const struct rtl_evto_packet *after, unsigned int wire_tags)
{
	int ret;
	if (!skb_vlan_tag_present(skb) && before->count == after->count &&
	    !memcmp(before->tag, after->tag, before->count * sizeof(before->tag[0]))) {
		skb->priority = after->priority;
		return 0;
	}
	skb_reset_mac_header(skb);
	skb->protocol = eth_hdr(skb)->h_proto;
	__vlan_hwaccel_clear_tag(skb);
	for (unsigned int i = 0; i < wire_tags; i++) {
		u16 tci;
		ret = __skb_vlan_pop(skb, &tci);
		if (ret) return ret;
	}
	for (unsigned int i = after->count; i; i--) {
		ret = __vlan_insert_tag(skb, htons(after->tag[i - 1].tpid), after->tag[i - 1].tci);
		if (ret) return ret;
		skb_postpush_rcsum(skb, skb->data + ETH_ALEN * 2, VLAN_HLEN);
	}
	skb->protocol = htons(after->count ? after->tag[0].tpid : after->protocol);
	skb->priority = after->priority;
	skb_reset_mac_len(skb);
	return 0;
}
#endif
