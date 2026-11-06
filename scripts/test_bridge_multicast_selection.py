#!/usr/bin/env python3
"""Execute Linux's real multicast output selection before native offload capture.

The purpose is to establish the required complete recipient set, including
router merging, include/block flags, topology filters and clone failures.
No hardware or automatic multicast offload is modeled by this test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def function(source, name):
    start=re.search(r'^(?:static )?(?:inline )?[^\n]*\b'+name+r'\([^;]*?\)\s*\{',source,re.M).start()
    end=source.index('{',start)+1
    depth=1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}')
        end+=1
    return source[start:end]+'\n'


parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
path=args.kernel/'net/bridge/br_forward.c'
source=path.read_text()
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint64_t u64;
#define CONFIG_ARCH_RTL9617C 1
#define BR_STATE_FORWARDING 3
#define BR_HAIRPIN_MODE 1
#define BR_MULTICAST_TO_UNICAST 2
#define BR_FLOOD 4
#define BR_MCAST_FLOOD 8
#define BR_BCAST_FLOOD 16
#define BR_PROXYARP 32
#define BR_PROXYARP_WIFI 64
#define BR_BPDU_FILTER 128
#define MCAST_INCLUDE 1
#define MDB_PG_FLAGS_BLOCKED 1
#define GFP_ATOMIC 0
#define rcu_dereference(v) (v)
#define unlikely(v) (v)
#define IS_ERR(v) ((uintptr_t)(v)>(uintptr_t)-4096)
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(v) ((intptr_t)(v))
#define BR_MCAST_DIR_TX 1
#define DEV_STATS_INC(dev,field) ((dev)->field++)
enum skb_drop_reason {SKB_DROP_REASON_NO_TX_TARGET,SKB_DROP_REASON_NOMEM,SKB_DROP_REASON_NOT_SPECIFIED};
struct net_device {unsigned int id,tx_dropped;};
struct net_bridge_vlan_group {bool allowed;};
struct net_bridge_port;
struct net_bridge {struct net_device *dev;struct net_bridge_port *port_list;};
struct net_bridge_port {
 struct net_device *dev;struct net_bridge *br;unsigned int flags,state;
 struct net_bridge_vlan_group vlan;bool hwdom,isolated,mst,neigh;struct net_bridge_port *next;
};
#define list_for_each_entry_rcu(p,head,member) for((p)=*(head);(p);(p)=(p)->next)
enum br_pkt_type {BR_PKT_UNICAST,BR_PKT_MULTICAST,BR_PKT_BROADCAST};
struct hlist_node {struct hlist_node *next;struct net_bridge_port *port;};
struct net_bridge_port_group {
 struct {struct net_bridge_port *port;} key;
 struct net_bridge_port_group *next;
 unsigned int filter_mode,flags;u8 eth_addr[6];
};
struct br_ip {unsigned int proto;bool star;};
struct net_bridge_mdb_entry {struct net_bridge_port_group *ports;struct br_ip addr;};
struct net_bridge_mcast {struct hlist_node *routers;bool handle_mode;};
struct sk_buff {struct net_device *dev,*bridge;bool cloned,miss,proxy;struct {u8 h_dest[6];} eth;};
#define eth_hdr(s) (&(s)->eth)
#define BR_INPUT_SKB_CB(skb) (&(struct {struct net_device *brdev;bool proxyarp_replied;}){(skb)->bridge,(skb)->proxy})
static unsigned int delivered[8],unicast[8],original,clones,drops,counted,fail_clone,clone_attempts;
static unsigned int selected[8],completed,aborted,captured;static bool active;
static u64 rtl9617c_bridge_mcast_capture(struct sk_buff *s,bool local){(void)s;(void)local;captured++;active=true;return 1;}
static void rtl9617c_bridge_mcast_select(struct sk_buff *s,struct net_device *d){(void)s;if(active)selected[d->id]++;}
static void rtl9617c_bridge_mcast_complete(u64 ticket,bool success){if(!ticket)return;assert(ticket==1&&active);active=false;if(success)completed++;else aborted++;}
static bool is_link_local_ether_addr(const u8 *a){return !memcmp(a,"\1\x80\xc2\0\0",5)&&a[5]<16;}
static bool br_is_neigh_suppress_enabled(struct net_bridge_port *p,u16 vid){assert(vid==100);return p->neigh;}
static struct net_bridge_vlan_group *nbp_vlan_group_rcu(const struct net_bridge_port *p){return (void *)&p->vlan;}
static bool br_mst_is_enabled(const struct net_bridge_port *p){return p->mst;}
static bool br_allowed_egress(struct net_bridge_vlan_group *v,const struct sk_buff *s){(void)s;return v->allowed;}
static bool nbp_switchdev_allowed_egress(const struct net_bridge_port *p,const struct sk_buff *s){(void)s;return p->hwdom;}
static bool br_skb_isolated(const struct net_bridge_port *p,const struct sk_buff *s){(void)s;return p->isolated;}
static u8 br_multicast_igmp_type(struct sk_buff *s){(void)s;return 0;}
static void nbp_switchdev_frame_mark_tx_fwd_to_hwdom(struct net_bridge_port *p,struct sk_buff *s){(void)p;(void)s;}
static void br_multicast_count(struct net_bridge *b,struct net_bridge_port *p,struct sk_buff *s,u8 t,int d){(void)b;(void)p;(void)s;(void)t;assert(d==1);counted++;}
static struct sk_buff *skb_clone(struct sk_buff *s,int f){(void)f;if(++clone_attempts==fail_clone)return NULL;struct sk_buff *n=malloc(sizeof(*n));assert(n);*n=*s;n->cloned=true;return n;}
static void __br_forward(const struct net_bridge_port *p,struct sk_buff *s,bool local){(void)local;delivered[p->dev->id]++;if(s->cloned){clones++;free(s);}else original++;}
static void kfree_skb_reason(struct sk_buff *s,enum skb_drop_reason r){assert(!s->cloned);(void)r;drops++;}
static struct hlist_node *br_multicast_get_first_rport_node(struct net_bridge_mcast *b,struct sk_buff *s){(void)s;return b->routers;}
static struct net_bridge_port *br_multicast_rport_from_node_skb(struct hlist_node *n,struct sk_buff *s){(void)s;return n?n->port:NULL;}
static struct hlist_node *hlist_next_rcu(struct hlist_node *n){return n->next;}
static bool br_multicast_should_handle_mode(struct net_bridge_mcast *b,unsigned int p){(void)p;return b->handle_mode;}
static bool br_multicast_is_star_g(struct br_ip *ip){return ip->star;}
static void br_tc_skb_miss_set(struct sk_buff *s,bool miss){s->miss=miss;}
/* MAC rewrite/copy is tested elsewhere; record that the actual selector took
 * this branch, which needs distinct hardware unicast-replica semantics. */
static void maybe_deliver_addr(struct net_bridge_port *p,struct sk_buff *s,const u8 *a,bool local){(void)s;(void)a;(void)local;unicast[p->dev->id]++;}
'''
for name in ('should_deliver','deliver_clone','maybe_deliver','br_multicast_flood','br_flood'):
    code+=function(source,name)
code+=r'''
static void reset(void){memset(delivered,0,sizeof(delivered));memset(unicast,0,sizeof(unicast));memset(selected,0,sizeof(selected));completed=aborted=original=clones=drops=counted=fail_clone=clone_attempts=captured=0;active=false;}
int main(void){
 struct net_device devices[5]={{.id=0},{.id=1},{.id=2},{.id=3},{.id=4}},bridge_dev={.id=7};
 struct net_bridge bridge={.dev=&bridge_dev};struct net_bridge_port ports[5];
 for(unsigned int i=0;i<5;i++)ports[i]=(struct net_bridge_port){.dev=&devices[i],.br=&bridge,.state=BR_STATE_FORWARDING,.vlan={true},.hwdom=true};
 /* Linux merges lists sorted in descending pointer order. */
 struct net_bridge_port_group groups[3]={
  {.key={&ports[4]}},{.key={&ports[2]}},{.key={&ports[1]}}};
 groups[0].next=&groups[1];groups[1].next=&groups[2];
 struct hlist_node routers[2]={{.port=&ports[3]},{.port=&ports[2]}};routers[0].next=&routers[1];
 struct net_bridge_mcast mcast={.routers=routers,.handle_mode=true};
 struct net_bridge_mdb_entry mdb={.ports=groups,.addr={0x800,true}};
 struct sk_buff packet={.dev=&devices[0],.bridge=&bridge_dev};
 unsigned int cases=0;
 for(unsigned int host=0;host<2;host++) {
  reset();br_multicast_flood(&mdb,&packet,&mcast,host,false);
  assert(delivered[1]==1&&delivered[2]==1&&delivered[3]==1&&delivered[4]==1);
  assert(!memcmp(selected,delivered,sizeof(selected))&&completed==1&&!aborted);
  assert(clones==3+host&&original==!host&&!drops&&counted==4);cases++;
 }
 /* A router remains eligible even when the same port's (*,G) membership
  * has INCLUDE/block flags. A non-router INCLUDE member is skipped. */
 groups[0].filter_mode=groups[1].filter_mode=MCAST_INCLUDE;
 groups[1].flags=MDB_PG_FLAGS_BLOCKED;
 reset();br_multicast_flood(&mdb,&packet,&mcast,false,false);
 assert(!delivered[4]&&delivered[2]==1&&delivered[1]==1&&delivered[3]==1);cases++;
 mdb.addr.star=false;reset();br_multicast_flood(&mdb,&packet,&mcast,false,false);
 assert(delivered[4]==1&&delivered[2]==1);cases++;
 mdb.addr.star=true;groups[0].filter_mode=groups[1].filter_mode=0;groups[1].flags=0;
 for(unsigned int filter=0;filter<5;filter++) {
  if(filter==0)ports[4].state=0;
  if(filter==1)ports[4].vlan.allowed=false;
  if(filter==2)ports[4].hwdom=false;
  if(filter==3)ports[4].isolated=true;
  if(filter==4)packet.dev=&devices[4];
  reset();br_multicast_flood(&mdb,&packet,&mcast,false,false);
  assert(!delivered[4]&&delivered[1]==1&&delivered[2]==1&&delivered[3]==1);cases++;
  ports[4].state=BR_STATE_FORWARDING;ports[4].vlan.allowed=ports[4].hwdom=true;ports[4].isolated=false;packet.dev=&devices[0];
 }
 ports[4].flags=BR_MULTICAST_TO_UNICAST;reset();br_multicast_flood(&mdb,&packet,&mcast,false,false);
 assert(unicast[4]==1&&!delivered[4]&&delivered[2]==1);cases++;
 assert(!completed&&aborted==1);
 ports[4].flags=0;
 /* The output set cannot be committed on the first successful clone: a
  * later allocation failure stops the actual traversal part way through. */
 for(unsigned int failed=1;failed<=3;failed++) {
  reset();fail_clone=failed;br_multicast_flood(&mdb,&packet,&mcast,false,false);
  assert(clones==failed-1&&drops==1&&!original);cases++;
  assert(!completed&&aborted==1);
 }
 reset();br_multicast_flood(NULL,&packet,&mcast,true,false);
 assert(packet.miss&&delivered[2]==1&&delivered[3]==1&&!delivered[1]&&!original);cases++;
 reset();mcast.routers=NULL;mdb.ports=NULL;
 br_multicast_flood(&mdb,&packet,&mcast,true,false);
 assert(completed==1&&!aborted&&!clones&&!original&&!drops);cases++;
 reset();br_multicast_flood(&mdb,&packet,&mcast,false,false);
 assert(!completed&&aborted==1&&drops==1);cases++;
 /* Unknown multicast and snooping-disabled fallback use br_flood, including
  * the exact per-port flood/proxy/STP/VLAN/isolation policy. */
 bridge.port_list=ports;
 for(unsigned i=0;i<5;i++){ports[i].flags=BR_MCAST_FLOOD|BR_FLOOD|BR_BCAST_FLOOD;ports[i].next=i==4?NULL:&ports[i+1];}
 for(unsigned host=0;host<2;host++)for(unsigned filter=0;filter<9;filter++) {
  unsigned flags=ports[4].flags;
  if(filter==0)ports[4].flags&=~BR_MCAST_FLOOD;
  if(filter==1)ports[4].flags|=BR_PROXYARP;
  if(filter==2){packet.proxy=true;ports[4].flags|=BR_PROXYARP_WIFI;}
  if(filter==3){packet.proxy=true;ports[4].neigh=true;}
  if(filter==4)ports[4].state=0;
  if(filter==5)ports[4].vlan.allowed=false;
  if(filter==6)ports[4].isolated=true;
  if(filter==7)ports[4].hwdom=false;
  reset();br_flood(&bridge,&packet,BR_PKT_MULTICAST,host,false,100);
  assert(captured==1&&completed==1&&!aborted&&delivered[1]&&delivered[2]&&delivered[3]);
  assert(delivered[4]==(filter==8)&&!memcmp(selected,delivered,sizeof(selected)));cases++;
  ports[4].flags=flags;ports[4].neigh=ports[4].isolated=packet.proxy=false;
  ports[4].state=BR_STATE_FORWARDING;ports[4].vlan.allowed=ports[4].hwdom=true;
 }
 for(unsigned failed=1;failed<=4;failed++) {
  reset();fail_clone=failed;br_flood(&bridge,&packet,BR_PKT_MULTICAST,true,false,100);
  assert(captured==1&&!completed&&aborted==1&&clones==failed-1);cases++;
 }
 for(unsigned i=0;i<5;i++)ports[i].flags=BR_FLOOD|BR_BCAST_FLOOD;
 reset();br_flood(&bridge,&packet,BR_PKT_MULTICAST,true,false,100);
 assert(captured==1&&completed==1&&!aborted&&!clones&&!original&&!drops);cases++;
 reset();br_flood(&bridge,&packet,BR_PKT_MULTICAST,false,false,100);
 assert(captured==1&&!completed&&aborted==1&&drops==1);cases++;
 for(unsigned kind=BR_PKT_UNICAST;kind<=BR_PKT_BROADCAST;kind++) {
  reset();br_flood(&bridge,&packet,kind,true,true,100);
  assert(!captured&&!completed&&!aborted);cases++;
  if(kind!=BR_PKT_MULTICAST){reset();br_flood(&bridge,&packet,kind,true,false,100);assert(!captured);cases++;}
 }
 printf("Linux multicast selection: %u complete-member, router/filter, local-copy and clone-failure cases passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='br-mcast-selection-') as name:
    root=Path(name);(root/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(root/'test.c'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
args.output.write_text(json.dumps(dict(kernel_source=str(path),sha256=hashlib.sha256(source.encode()).hexdigest(),cases=45,
    scope='Actual Linux br_multicast_flood/br_flood/maybe_deliver/should_deliver/clone control flow and native transaction callbacks; topology, allocation and output sinks modeled. Does not prove physical hardware forwarding.'),indent=2)+'\n')
