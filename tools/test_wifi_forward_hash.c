// SPDX-License-Identifier: GPL-2.0-only
/* Isolated real skb/flow-dissector/FQ test; never registers or sends traffic. */
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/tcp.h>
#include <linux/jhash.h>
#include <linux/bitmap.h>
#include <net/flow_dissector.h>
#include <net/fq_impl.h>
#include "forward-hash-under-test.h"

static struct net_device *device;
static struct sk_buff *packet(unsigned type, unsigned port, unsigned id, bool internal)
{
 struct sk_buff *skb=alloc_skb(160,GFP_KERNEL);
 struct ethhdr *eth;struct iphdr *ip;struct ipv6hdr *ip6;struct tcphdr *tcp;
 unsigned l3=ETH_HLEN+(type==2?4:type==3?8:0),iplen=type==1?40:20,len=l3+iplen+28;
 if(!skb)return NULL;
 memset(skb_put(skb,len),0,len);skb->dev=device;
 eth=(void*)skb->data;eth->h_dest[0]=2;eth->h_dest[5]=2;eth->h_source[0]=2;eth->h_source[5]=1;
 eth->h_proto=htons(type==1?ETH_P_IPV6:type==2?ETH_P_8021Q:type==3?ETH_P_8021AD:ETH_P_IP);
 if(type>=2){put_unaligned_be16(123,skb->data+14);put_unaligned_be16(type==3?ETH_P_8021Q:ETH_P_IP,skb->data+16);}
 if(type==3){put_unaligned_be16(456,skb->data+18);put_unaligned_be16(ETH_P_IP,skb->data+20);}
 if(type==1){ip6=(void*)(skb->data+l3);ip6->version=6;ip6->nexthdr=IPPROTO_TCP;ip6->payload_len=htons(28);ip6->saddr.s6_addr[0]=0x20;ip6->saddr.s6_addr[15]=1;ip6->daddr.s6_addr[0]=0x20;ip6->daddr.s6_addr[15]=2;}
 else {ip=(void*)(skb->data+l3);ip->version=4;ip->ihl=5;ip->tot_len=htons(iplen+28);ip->protocol=IPPROTO_TCP;ip->saddr=htonl(0xc0000201);ip->daddr=htonl(0xc0000202);}
 tcp=(void*)(skb->data+l3+iplen);tcp->source=htons(port);tcp->dest=htons(5201);tcp->doff=5;tcp->seq=htonl(id);
 skb->protocol=internal?htons(ETH_P_802_3):eth->h_proto;
 skb_reset_mac_header(skb);skb_set_network_header(skb,internal?0:ETH_HLEN);
 skb->priority=256;skb->ip_summed=CHECKSUM_UNNECESSARY;
 return skb;
}
static void release(struct fq *fq,struct fq_tin *tin,struct fq_flow *flow,struct sk_buff *skb){kfree_skb(skb);}
static struct sk_buff *dequeue(struct fq *fq,struct fq_tin *tin,struct fq_flow *flow){return fq_flow_dequeue(fq,flow);}
static int ordering(unsigned type,unsigned port,bool fixed,bool *ordered)
{
 struct fq fq;struct fq_tin tin={};struct sk_buff *skb;
 unsigned i,l3=ETH_HLEN+(type==2?4:type==3?8:0),iplen=type==1?40:20;int ret;
 ret=fq_init(&fq,1024);if(ret)return ret;fq_tin_init(&tin);*ordered=true;
 for(i=1;i<=3;i++){
  u8 bytes[160];__be16 protocol;u16 network_header;
  skb=packet(type,port,i,i==2);if(!skb){ret=-ENOMEM;goto out;}
  memcpy(bytes,skb->data,skb->len);protocol=skb->protocol;network_header=skb->network_header;
  skb_get_hash(skb); /* Existing RX/bridge metadata, as in the live trace. */
  if(fixed)ieee80211_prepare_forward_hash(skb);
  if(memcmp(bytes,skb->data,skb->len)||skb->protocol!=protocol||skb->network_header!=network_header||skb->priority!=256||skb->ip_summed!=CHECKSUM_UNNECESSARY){kfree_skb(skb);ret=-EINVAL;goto out;}
  spin_lock_bh(&fq.lock);fq_tin_enqueue(&fq,&tin,fq_flow_idx(&fq,skb),skb,release);spin_unlock_bh(&fq.lock);
 }
 for(i=1;i<=3;i++){
  struct tcphdr *tcp;
  spin_lock_bh(&fq.lock);skb=fq_tin_dequeue(&fq,&tin,dequeue);spin_unlock_bh(&fq.lock);
  if(!skb){ret=-EINVAL;goto out;}
  tcp=(void*)(skb->data+l3+iplen);if(ntohl(tcp->seq)!=i)*ordered=false;kfree_skb(skb);
 }
 ret=0;
out:
 spin_lock_bh(&fq.lock);fq_tin_reset(&fq,&tin,release);fq_reset(&fq,release);spin_unlock_bh(&fq.lock);return ret;
}
static int __init run(void)
{
 unsigned type,port,cases=0,old_reorders=0;bool old,new;int ret=0;
 device=alloc_netdev(0,"hash-test%d",NET_NAME_UNKNOWN,ether_setup);if(!device)return -ENOMEM;
 for(type=0;type<4;type++)for(port=20000;port<20032;port++){
  ret=ordering(type,port,false,&old);if(ret)goto out;
  ret=ordering(type,port,true,&new);if(ret||!new){ret=-EINVAL;goto out;}
  old_reorders+=!old;cases++;
 }
 if(!old_reorders){ret=-EINVAL;goto out;}
 pr_info("Forward hash: %u real skb/FQ IPv4/IPv6/VLAN/QinQ cases ordered; old implementation reordered %u; packet/protocol/priority/checksum unchanged\n",cases,old_reorders);
out:
 free_netdev(device);return ret;
}
static void __exit stop(void){}
module_init(run);module_exit(stop);MODULE_LICENSE("GPL");MODULE_DESCRIPTION("Isolated forwarding flow hash regression test");
