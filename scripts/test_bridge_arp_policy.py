#!/usr/bin/env python3
"""Show why a MAC/VLAN learned key cannot replace actual Linux ARP decisions."""
import argparse
from pathlib import Path
import re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--kernel',type=Path,required=True);a=p.parse_args()
text=(a.kernel/'net/bridge/br_arp_nd_proxy.c').read_text()
def function(name):
 m=re.search(r'^void '+name+r'\([^;]*?\)\s*\{',text,re.M);start=m.start();end=text.index('{',start)+1;depth=1
 while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[start:end]+'\n'
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <arpa/inet.h>
typedef uint8_t u8;typedef uint16_t u16,__be16;typedef uint32_t __be32;
#define ETH_P_IP 0x0800
#define ARPOP_REQUEST 1
#define ARPOP_REPLY 2
#define ARPOP_RREQUEST 3
#define ARPOP_RREPLY 4
#define IFF_NOARP 1
#define NUD_VALID 2
#define BR_PROXYARP 4
#define BR_PROXYARP_WIFI 8
#define BROPT_NEIGH_SUPPRESS_ENABLED 1
#define READ_ONCE(v) (v)
#define is_unicast_ether_addr(p) (!((p)[0]&1))
#define ipv4_is_loopback(ip) ((ntohl(ip)>>24)==127)
#define ipv4_is_multicast(ip) ((ntohl(ip)>>28)==14)
struct net_device {unsigned flags,addr_len;};
struct net_bridge_port {unsigned flags;bool suppress;};
struct net_bridge {struct net_device *dev;bool suppress;};
struct net_bridge_fdb_entry {struct net_bridge_port *dst;};
struct neighbour {unsigned nud_state;u8 ha[6];};
struct arphdr {__be16 ar_hrd,ar_pro;u8 ar_hln,ar_pln;__be16 ar_op;};
struct ethhdr {u8 h_dest[6],h_source[6];__be16 h_proto;};
struct sk_buff {struct ethhdr eth;unsigned char arp[28];__be16 vlan_proto;u16 vlan;struct net_device *dev;struct {unsigned proxyarp_replied;}cb;};
#define BR_INPUT_SKB_CB(s) (&(s)->cb)
#define eth_hdr(s) (&(s)->eth)
#define arp_hdr(s) ((struct arphdr *)(s)->arp)
static int arp_tbl;static unsigned lookups,releases,replies;static __be32 known_ip,local_ip;static struct neighbour n;
static struct net_bridge_fdb_entry fdb;static bool has_fdb=true,has_vlan=true;
static unsigned arp_hdr_len(struct net_device *d){return sizeof(struct arphdr)+2*d->addr_len+8;}
static bool pskb_may_pull(struct sk_buff *s,unsigned len){(void)s;return len<=28;}
static bool br_opt_get(struct net_bridge *b,unsigned flag){assert(flag==1);return b->suppress;}
static bool br_is_neigh_suppress_enabled(const struct net_bridge_port *p,u16 vid){(void)vid;return p&&p->suppress;}
static bool br_is_local_ip(struct net_device *d,__be32 ip){(void)d;return local_ip==ip;}
static struct net_device *__vlan_find_dev_deep_rcu(struct net_device *d,__be16 p,u16 vid){assert(p==htons(0x8100)&&vid==100);return has_vlan?d:NULL;}
static struct neighbour *neigh_lookup(void *t,const __be32 *ip,struct net_device *d){assert(t==&arp_tbl&&d);lookups++;return *ip==known_ip?&n:NULL;}
static void neigh_release(struct neighbour *n){assert(n);releases++;}
static struct net_bridge_fdb_entry *br_fdb_find_rcu(struct net_bridge *br,const u8 *mac,u16 vid){(void)br;(void)vid;assert(!memcmp(mac,n.ha,6));return has_fdb?&fdb:NULL;}
static u16 skb_vlan_tag_get(struct sk_buff *s){return s->vlan;}
static void br_arp_send(struct net_bridge *b,struct net_bridge_port *p,struct net_device *d,__be32 sip,__be32 tip,const u8 *sha,const u8 *ha,const u8 *dst,u16 proto,u16 tci){
 (void)b;(void)p;(void)d;(void)sip;assert(tip==known_ip);assert(!memcmp(ha,n.ha,6)&&!memcmp(sha,dst,6));assert((!proto&&!tci)||(proto==htons(0x8100)&&tci==100));replies++;
}
#include "rtl9617c-fe-format.h"
'''
code+=function('br_do_proxy_suppress_arp')
code+=r'''
static void packet(struct sk_buff *s,unsigned op,unsigned ip){
 memset(s,0,sizeof(*s));memcpy(s->eth.h_dest,"\2\0\0\0\0\2",6);memcpy(s->eth.h_source,"\2\0\0\0\0\1",6);
 s->eth.h_proto=htons(0x0806);s->vlan_proto=htons(0x8100);s->vlan=100;
 struct arphdr h={.ar_hrd=htons(1),.ar_pro=htons(ETH_P_IP),.ar_hln=6,.ar_pln=4,.ar_op=htons(op)};memcpy(s->arp,&h,8);
 memcpy(s->arp+8,s->eth.h_source,6);__be32 sip=htonl(0x0a000001),tip=htonl(ip);memcpy(s->arp+14,&sip,4);memcpy(s->arp+24,&tip,4);
}
int main(void){
 struct net_device dev={.addr_len=6};struct net_bridge br={.dev=&dev};struct net_bridge_port in={.flags=BR_PROXYARP},out={0};fdb.dst=&out;
 n.nud_state=NUD_VALID;memcpy(n.ha,"\2\0\0\0\0\2",6);known_ip=htonl(0x0a000002);
 unsigned key[2][RTL_FE_KEY_WORDS];unsigned cases=0;
 for(unsigned vid=0;vid<2;vid++){
  struct sk_buff s;
  for(unsigned known=0;known<2;known++){
   packet(&s,ARPOP_REQUEST,known?0x0a000002:0x0a000003);s.dev=&dev;replies=0;
   rtl_fe_bridge_key(key[known],s.eth.h_source,s.eth.h_dest,0x0806,6,0,59);
   struct rtl_fe_vlan tag[2]={{.vid=263,.tpid=1}};rtl_fe_vlan_key(key[known],tag,1);
   br_do_proxy_suppress_arp(&s,&br,vid?100:0,&in);
   assert(replies==known&&s.cb.proxyarp_replied==known);cases++;
  }
  assert(!memcmp(key[0],key[1],sizeof(key[0])));
  for(unsigned op=2;op<=4;op++){
   packet(&s,op,0x0a000002);s.dev=&dev;replies=0;br_do_proxy_suppress_arp(&s,&br,vid?100:0,&in);
   assert(!replies&&!s.cb.proxyarp_replied);cases++;
  }
 }
 /* Broadcast neighbour suppression depends on the target cache too,
  * even with the same MAC addresses and no proxy reply generation. */
 br.suppress=true;in.flags=0;out.suppress=true;
 for(unsigned known=0;known<2;known++){
  struct sk_buff s;packet(&s,ARPOP_REQUEST,known?0x0a000002:0x0a000003);memset(s.eth.h_dest,255,6);s.dev=&dev;replies=0;
  br_do_proxy_suppress_arp(&s,&br,0,&in);assert(s.cb.proxyarp_replied==known&&replies==known);cases++;
 }
 assert(lookups>0&&releases>0);
 printf("ARP policy: %u actual Linux proxy/suppression/opcode cases; distinct forwarding decisions collide in the MAC/VLAN key\n",cases);
}
'''
with tempfile.TemporaryDirectory() as folder:
 f=Path(folder);(f/'test.c').write_text(code)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'),str(f/'test.c'),'-o',str(f/'test')],check=True)
 subprocess.run([str(f/'test')],check=True)
