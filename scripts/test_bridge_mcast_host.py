#!/usr/bin/env python3
"""Execute actual QM/bridge functions at the hardware host-copy boundary.

DMA, VLAN/netfilter helpers and network delivery are modeled. The production
QM poll, metadata decoder, bridge finish/local delivery and callback core run.
"""
import argparse
import ast
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/include'
DRIVERS = ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--kernel', type=Path, required=True)
args = p.parse_args()


def function(source, name):
    m = re.search(r'^(?:static\s+)?(?:inline )?\w+[ *\n]+\b'+name+r'\([^;]*?\)\s*\{', source, re.M)
    start = m.start()
    end = source.index('{', start)+1
    depth = 1
    while depth:
        depth += (source[end] == '{')-(source[end] == '}')
        end += 1
    return source[start:end]+'\n'


def run(code, name):
    with tempfile.TemporaryDirectory(prefix='mcast-host-'+name+'-') as tmp:
        tmp = Path(tmp)
        (tmp/'test.c').write_text(code)
        subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror',
                        '-Wno-unused-function','-Wno-unused-variable','-Wno-unused-parameter','-Wno-misleading-indentation',
                        '-fsanitize=address,undefined','-I'+str(DRIVERS),
                        '-I'+str(INCLUDE),'-I'+str(INCLUDE/'linux'),
                        str(tmp/'test.c'),'-o',str(tmp/'test')], check=True)
        subprocess.run([str(tmp/'test')], check=True)


fixture = ast.parse((ROOT/'scripts/test_ni_rx_credit.py').read_text())
code = next(ast.literal_eval(n.value) for n in fixture.body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'common' for t in n.targets))
code = code[:code.index('static void skb_reset_mac_header')]+code[code.index('struct rtl9617c_ni_wifi_endpoint'):]
code = code[:code.index('static unsigned rtl9617c_bridge_host_group')]+code[code.index('struct rtl_qm_rx {'):]
code = code.replace('u8 storage[128]', 'u8 storage[2048]')
code = code.replace('struct rtl_qm_rx {', '''
#include <errno.h>
#include "linux/rtl9617c-bridge.h"
static struct sk_buff *napi_alloc_skb(struct napi_struct *,unsigned);
static void *skb_put(struct sk_buff *,unsigned);
static void dev_kfree_skb_any(struct sk_buff *);
#include "rtl9617c-fttr-rx.h"
static int rtl9617c_bridge_mcast_receive_test(struct sk_buff *,unsigned,unsigned);
#define rtl9617c_bridge_mcast_receive rtl9617c_bridge_mcast_receive_test
static void netif_receive_skb(struct sk_buff *);
struct rtl_qm_rx {struct rtl_fttr_rx fttr_rx[8];unsigned fttr_epoch;
''')
code = code.replace('struct rtl9617c_ni_endpoint *pon;', 'struct rtl9617c_ni_endpoint *pon,*fttr;')
code = code.replace('bool profile;', 'bool profile,deepq_configured;')
code = code[:code.index('static u64 ktime_get_ns(')]+r'''
#define SKB_EXT_RTL9617C_BRIDGE 0
static struct rtl9617c_bridge_frame host_frame;
static const struct rtl9617c_bridge_frame *skb_ext_find(struct sk_buff *s,int id){(void)s;assert(!id);return &host_frame;}
static unsigned retired,host_calls,local_calls,gro_calls,radio_calls,expect_group,expect_source,expect_cos;
static unsigned expected_length,live;
static bool reject_host,direct_copy,epon_copy,ethernet_copy;
static u8 expected[1600];
static struct net_device input;
static u64 ktime_get_ns(void){return 1;}
static u32 readl(void *p){return get_unaligned_le32(p);}
static void writel(u32 v,void *p){put(v,p);}
static void dma_sync_single_for_cpu(void *d,dma_addr_t a,unsigned n,int direction){(void)d;(void)a;assert(n&&direction==DMA_FROM_DEVICE);}
static int rtl_qm_try_push(void *context,unsigned address,unsigned eq){(void)context;(void)address;assert(eq==4);retired++;return 0;}
static bool rtl_rx_checksum_valid(u32 c,const void *p,unsigned n){(void)c;(void)p;(void)n;return false;}
static struct sk_buff *napi_alloc_skb(struct napi_struct *n,unsigned length){(void)n;assert(length==expected_length);struct sk_buff *s=calloc(1,sizeof(*s));assert(s);s->data=s->storage;live++;return s;}
static void *skb_put(struct sk_buff *s,unsigned n){void *p=s->data+s->len;s->len+=n;return p;}
static void dev_kfree_skb_any(struct sk_buff *s){assert(live);live--;free(s);}
static bool netif_running(struct net_device *d){return d->running;}
static unsigned eth_type_trans(struct sk_buff *s,struct net_device *d){s->dev=d;s->data+=14;s->len-=14;return get_unaligned_be16(s->data-2);}
static int rtl9617c_bridge_mcast_receive_test(struct sk_buff *s,unsigned group,unsigned source){
 assert(group==expect_group&&source==expect_source&&active->read[expect_cos]==8&&readl(active->ni+EPP_READ+expect_cos*4)==8);
 assert(s->len==expected_length&&!memcmp(s->data,expected,s->len));host_calls++;
 host_frame.pon_gem=source==7?55:0;host_frame.wifi_id=(source==18||direct_copy)?1:0;
 host_frame.epon=epon_copy;host_frame.epon_llid=expect_cos&1?31:0;if(epon_copy)host_frame.pon_gem=0;
 assert(s->priority==expect_cos);
 if(direct_copy && (expect_cos&1))s->priority=256+expect_cos;
 s->dev=&input;return reject_host?-ESTALE:ethernet_copy?2:direct_copy?1:0;
}
static void skb_reset_mac_header(struct sk_buff *s){(void)s;}
#define eth_hdr(s) ((struct {u8 da[6],sa[6];u16 h_proto;} *)(s)->data)
static u16 __vlan_get_protocol(struct sk_buff *s,u16 protocol,int *depth){while(protocol==__builtin_bswap16(0x8100)){protocol=__builtin_bswap16(get_unaligned_be16(s->data+*depth+2));*depth+=4;}return protocol;}
static void skb_set_network_header(struct sk_buff *s,int depth){assert((unsigned)depth==14+(expected_length-1514));(void)s;}
static void dev_queue_xmit(struct sk_buff *s){assert(ethernet_copy&&s->dev==&input&&s->priority==expect_cos&&s->len==expected_length);assert(!memcmp(s->data,expected,s->len));radio_calls++;dev_kfree_skb_any(s);}
static void netif_receive_skb(struct sk_buff *s){
 assert(active->read[expect_cos]==8&&s->dev==&input&&s->priority==expect_cos&&s->protocol==get_unaligned_be16(expected+12));
 assert(s->len==expected_length-14&&!memcmp(s->data,expected+14,s->len));local_calls++;dev_kfree_skb_any(s);
}
static void napi_gro_receive(struct napi_struct *n,struct sk_buff *s){(void)n;gro_calls++;dev_kfree_skb_any(s);}
static void pon_receive(struct rtl9617c_ni_endpoint *e,struct napi_struct *n,struct sk_buff *s,unsigned src){
 (void)e;(void)n;assert(s->len==expected_length&&!memcmp(s->data,expected,s->len));
 if(host_calls){assert(src==(7U|((epon_copy?host_frame.epon_llid:55U)<<8))&&s->priority==0&&host_calls==1);local_calls++;}
 else {assert(src==7&&!host_calls);gro_calls++;}
 dev_kfree_skb_any(s);
}
static void p6_receive(struct rtl9617c_ni_endpoint *e,struct napi_struct *n,struct sk_buff *s,unsigned src){(void)e;(void)n;assert(src==6);gro_calls++;dev_kfree_skb_any(s);}
static void radio_receive(struct rtl9617c_ni_wifi_endpoint *e,struct napi_struct *n,struct sk_buff *s,bool f){(void)n;assert(f&&e->netdev==s->dev&&s->len==expected_length&&s->priority==(expect_cos&1?256+expect_cos:expect_cos)&&!memcmp(s->data,expected,s->len));assert(active->read[expect_cos]==8&&readl(active->ni+EPP_READ+expect_cos*4)==8);radio_calls++;dev_kfree_skb_any(s);}
'''
qm = (DRIVERS/'rtl9617c-qm.c').read_text()
code += qm[qm.index('static void rtl_qm_rx_retire('):qm.index('\nssize_t rtl_qm_rx_state(')]
code += r'''
int main(void){
 unsigned cases=0;
 /* Decoder domain: only native CPU0, physical LAN and nonzero group. */
 for(unsigned dst=0;dst<64;dst++)for(unsigned src=0;src<64;src++)for(unsigned pol=0;pol<512;pol++)
  assert(rtl9617c_bridge_host_group(pol<<18,dst<<3|src<<9)==
   ((dst==16&&(src<=4||src==6||src==7||src==18)&&(pol&256))?pol&255:0));
 for(unsigned full=0;full<2;full++)for(unsigned tags=0;tags<3;tags++)
 for(unsigned cos=0;cos<8;cos++)for(unsigned fault=0;fault<10;fault++) {
  struct net_device owner={.running=true};struct napi_struct napi={.dev=&owner};
  struct rtl9617c_ni_wifi_endpoint radio={.netdev=&input,.receive=radio_receive};
  struct rtl9617c_ni_endpoint p6={.receive=p6_receive},pon={.receive=pon_receive};
  struct rtl_qm_rx rx={.pon=&pon,.fttr=&p6,.memory_dma=0x04000000,.ring_dma=0x06000000,.device=&owner};active=&rx;
  rx.memory=calloc(1,RTL_QM_MEMORY_BYTES);rx.ring_memory=calloc(1,8192);rx.ni=calloc(1,65536);
  rx.lan[0]=rx.lan[4]=&input;rx.wifi[1]=&radio;input.running=true;
  retired=host_calls=local_calls=gro_calls=radio_calls=0;reject_host=fault==1;direct_copy=fault>=5&&fault<8;epon_copy=fault==8;
  ethernet_copy=fault==9;
  expect_source=cos%5==4?18:cos%5==3?7:cos%5==2?6:cos%5==1?4:0;
  if(fault==2 && expect_source==18)expect_source=0; /* Ordinary radio misses tested by Wi-Fi RX fixture. */
  if(fault==4){expect_source=18;rx.wifi[1]=NULL;}expect_group=cos+1;expect_cos=cos;expected_length=1514+4*tags;
  if(fault==6)rx.wifi[1]=NULL;
  if(fault==7)radio.netdev=&owner; /* A reused ID must not redirect a stale copy. */
  if(fault==8)expect_source=7;
  memset(expected,0x55,sizeof(expected));expected[0]=1;expected[6]=2;expected[12]=tags?0x81:8;expected[13]=0;
  for(unsigned t=0;t<tags;t++){expected[14+4*t]=0xb1;expected[15+4*t]=0x23;expected[16+4*t]=t+1<tags?0x81:8;expected[17+4*t]=0;}
  unsigned header=full?16:8,pol=fault==2?0:256+expect_group;
  u8 *data=rx.memory+8192+RTL_QM_HEADROOM;
  put(__builtin_bswap32(pol<<18|(full?BIT(31):0)),data);
  put(__builtin_bswap32((expected_length+(full?8:0))<<15|expect_source<<9|16<<3|cos),data+4);
  memcpy(data+header,expected,expected_length);
  put(rx.memory_dma+8192+(fault==3?0x14:0x54),rx.ring_memory+cos*1024);
  /* An EPP payload resembling a radio ID must not steal a host copy. */
  put(fault==2?0:1<<14,rx.ring_memory+cos*1024+4);
  put(8,rx.ni+EPP_WRITE+cos*4);
  assert(rtl_qm_rx_poll(&rx,&napi,1)==1&&!live&&retired==1);
  assert(host_calls==(fault<2||fault>=4)&&local_calls==(fault==0||fault==8)&&gro_calls==(fault==2)&&radio_calls==(fault==5||fault==9));
  assert(owner.stats.rx_dropped==(fault==1||fault==4||fault==6||fault==7)&&owner.stats.rx_errors==(fault==3));
  free(rx.memory);free(rx.ring_memory);free(rx.ni);cases++;
 }
 printf("QM host copies: %u actual poll cases, 2M decoder inputs, VLAN/COS, both headers, early credit, stale drop and ordinary miss separation passed\n",cases);
}
'''
run(code, 'qm')

# Execute actual bridge finish and pass-up. Model only their kernel helpers;
# native core callback dispatch/one-bridge consumption runs unchanged too.
code = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#include "linux/rtl9617c-bridge.h"
#define CONFIG_ARCH_RTL9617C 1
#define CONFIG_INET 0
#define IS_ENABLED(x) (x)
#define IFF_PROMISC 256
#define IFF_ALLMULTI 512
#define BR_STATE_DISABLED 0
#define BR_STATE_LEARNING 2
#define BR_STATE_FORWARDING 3
#define BR_PORT_LOCKED 1
#define BR_PORT_MAB 2
#define BR_LEARNING 4
#define BR_ISOLATED 8
#define BR_FDB_LOCKED 0
#define BR_FDB_LOCAL 1
#define BR_FDB_ADDED_BY_USER 2
#define BROPT_NEIGH_SUPPRESS_ENABLED 0
#define BROPT_FDB_LOCAL_VLAN_0 1
#define ETH_P_ARP 0x806
#define ETH_P_RARP 0x8035
#define ETH_P_IPV6 0x86dd
#define IPPROTO_ICMPV6 58
#define BIT(n) (1U<<(n))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define unlikely(x) (x)
#define EXPORT_SYMBOL_GPL(x)
#define SKB_EXT_RTL9617C_BRIDGE 0
#define BR_INPUT_SKB_CB(s) (&(s)->cb)
#define BR_INPUT_SKB_CB_MROUTERS_ONLY(s) ((s)->routers_only)
#define DEV_STATS_INC(d,n) ((d)->n++)
#define NET_RX_DROP 1
#define NFPROTO_BRIDGE 7
#define NF_BR_LOCAL_IN 1
#define BR_MCAST_DIR_TX 1
#define NF_HOOK(p,h,n,sk,s,in,out,fn) hook_local((n),(sk),(s),(fn))
#define htons(x) __builtin_bswap16(x)
struct net {int unused;};struct sock {int unused;};
struct net_device {unsigned flags,multicast;void *priv;};
struct net_bridge_vlan_group {bool allowed;};struct net_bridge_vlan {int unused;};
struct net_bridge_mcast {int unused;};struct net_bridge_mcast_port {int unused;};
struct net_bridge {struct net_device *dev;struct net_bridge_mcast multicast_ctx;struct net_bridge_vlan_group vg;};
struct net_bridge_port {struct net_bridge *br;unsigned state,flags;struct net_bridge_mcast_port multicast_ctx;struct net_bridge_vlan_group vg;};
struct net_bridge_fdb_entry {struct net_bridge_port *dst;unsigned long flags,used;};
struct net_bridge_mdb_entry {bool host_joined;};
struct ethhdr {u8 h_dest[6],h_source[6];};
struct ipv6hdr {u8 nexthdr;};struct nd_msg {int unused;};
struct sk_buff {struct net_device *dev;struct {struct net_device *brdev;bool promisc,src_port_isolated;} cb;
 struct ethhdr eth;struct rtl9617c_bridge_frame frame;unsigned len,protocol;bool routers_only,offload_fwd_mark;};
enum skb_drop_reason {SKB_DROP_REASON_NOT_SPECIFIED,SKB_DROP_REASON_BRIDGE_INGRESS_STP_STATE};
enum br_pkt_type {BR_PKT_UNICAST,BR_PKT_MULTICAST,BR_PKT_BROADCAST};
static struct net net;
static struct net_bridge_port port;
static struct net_bridge_mdb_entry mdb;
static struct net_bridge_fdb_entry fdb;
static bool have_mdb,querier,router,ingress,filter,stale,allocation_fail;
static unsigned delivered,drops,forwarded,flooded,selected,vlan_calls,local_hooks,vid,validated;
static unsigned long jiffies=1;
static void rcu_read_lock(void){}static void rcu_read_unlock(void){}
#define rcu_dereference(x) (x)
static void *skb_ext_find(struct sk_buff *s,int id){assert(!id);return &s->frame;}
static void *skb_ext_add(struct sk_buff *s,int id){return allocation_fail?NULL:skb_ext_find(s,id);}
static void *netdev_priv(struct net_device *d){return d->priv;}
static struct net *dev_net(struct net_device *d){(void)d;return &net;}
static void br_drop_fake_rtable(struct sk_buff *s){(void)s;}
static int netif_receive_skb(struct sk_buff *s){assert(!s->frame.multicast_host_group&&!s->offload_fwd_mark);delivered++;return 0;}
static void dev_sw_netstats_rx_add(struct net_device *d,unsigned n){(void)d;(void)n;}
static struct net_bridge_vlan_group *br_vlan_group_rcu(struct net_bridge *b){return &b->vg;}
static void br_switchdev_frame_unmark(struct sk_buff *s){s->offload_fwd_mark=false;}
static bool br_allowed_egress(struct net_bridge_vlan_group *g,struct sk_buff *s){(void)s;return g->allowed;}
static void kfree_skb(struct sk_buff *s){(void)s;drops++;}
static void kfree_skb_reason(struct sk_buff *s,enum skb_drop_reason r){(void)r;kfree_skb(s);}
static struct sk_buff *br_handle_vlan(struct net_bridge *b,void *p,struct net_bridge_vlan_group *g,struct sk_buff *s){(void)b;(void)p;(void)g;vlan_calls++;return s;}
static void br_multicast_count(struct net_bridge *b,void *p,struct sk_buff *s,unsigned t,unsigned d){(void)b;(void)p;(void)s;(void)t;(void)d;}
static unsigned br_multicast_igmp_type(struct sk_buff *s){(void)s;return 0;}
static int hook_local(struct net *n,struct sock *sk,struct sk_buff *s,int (*fn)(struct net *,struct sock *,struct sk_buff *)){
 local_hooks++;if(!filter){kfree_skb(s);return 0;}return fn(n,sk,s);
}
static struct net_bridge_port *br_port_get_rcu(struct net_device *d){(void)d;return &port;}
static bool br_mst_is_enabled(struct net_bridge_port *p){(void)p;return false;}
static struct net_bridge_vlan_group *nbp_vlan_group_rcu(struct net_bridge_port *p){return &p->vg;}
static bool br_allowed_ingress(struct net_bridge *b,struct net_bridge_vlan_group *g,struct sk_buff *s,u16 *v,u8 *state,struct net_bridge_vlan **vl){(void)b;(void)g;(void)state;*vl=NULL;*v=vid;if(!ingress)kfree_skb(s);return ingress;}
static struct ethhdr *eth_hdr(struct sk_buff *s){return &s->eth;}
static struct net_bridge_fdb_entry *br_fdb_find_rcu(struct net_bridge *b,const u8 *m,u16 v){(void)b;(void)m;(void)v;return &fdb;}
static bool test_bit(unsigned b,const unsigned long *v){return (*v>>b)&1;}
static void br_fdb_update(struct net_bridge *b,struct net_bridge_port *p,const u8 *m,u16 v,unsigned f){(void)b;(void)p;(void)m;(void)v;(void)f;}
static void nbp_switchdev_frame_mark(struct net_bridge_port *p,struct sk_buff *s){(void)p;s->offload_fwd_mark=true;}
static bool is_multicast_ether_addr(const u8 *m){return m[0]&1;}
static bool is_broadcast_ether_addr(const u8 *m){return m[0]==255;}
static int br_multicast_rcv(struct net_bridge_mcast **b,struct net_bridge_mcast_port **p,struct net_bridge_vlan *v,struct sk_buff *s,u16 id){(void)b;(void)p;(void)v;(void)s;(void)id;return 0;}
static void br_do_proxy_suppress_arp(struct sk_buff *s,struct net_bridge *b,u16 v,struct net_bridge_port *p){(void)s;(void)b;(void)v;(void)p;assert(0);}
static bool ipv6_mod_enabled(void){return false;}
static bool br_opt_get(struct net_bridge *b,int opt){(void)b;(void)opt;return false;}
static bool pskb_may_pull(struct sk_buff *s,unsigned n){(void)s;(void)n;return false;}
static struct ipv6hdr *ipv6_hdr(struct sk_buff *s){(void)s;return NULL;}
static struct nd_msg *br_is_nd_neigh_msg(struct sk_buff *s,struct nd_msg *m){(void)s;(void)m;return NULL;}
static void br_do_suppress_nd(struct sk_buff *s,struct net_bridge *b,u16 v,struct net_bridge_port *p,struct nd_msg *m){(void)s;(void)b;(void)v;(void)p;(void)m;assert(0);}
static struct net_bridge_mdb_entry *br_mdb_entry_skb_get(struct net_bridge_mcast *b,struct sk_buff *s,u16 v){(void)b;(void)s;(void)v;return have_mdb?&mdb:NULL;}
static bool br_multicast_querier_exists(struct net_bridge_mcast *b,struct ethhdr *e,struct net_bridge_mdb_entry *m){(void)b;(void)e;(void)m;return querier;}
static bool br_multicast_is_router(struct net_bridge_mcast *b,struct sk_buff *s){(void)b;(void)s;return router;}
static void br_forward(struct net_bridge_port *p,struct sk_buff *s,bool l,bool o){(void)p;(void)s;(void)l;(void)o;forwarded++;}
static void br_flood(struct net_bridge *b,struct sk_buff *s,enum br_pkt_type t,bool l,bool o,u16 v){(void)b;(void)s;(void)t;(void)l;(void)o;(void)v;flooded++;}
static void br_multicast_flood(struct net_bridge_mdb_entry *m,struct sk_buff *s,struct net_bridge_mcast *b,bool l,bool o){(void)m;(void)s;(void)b;(void)l;(void)o;selected++;}
static int host_local(void *ctx,struct sk_buff *s,struct net_device *b,u16 v){(void)ctx;assert(s->dev!=b&&v==vid);validated++;return stale?-ESTALE:1;}
static struct rtl9617c_bridge_mcast_ops ops={.host_local=host_local};
static const struct rtl9617c_bridge_mcast_ops *rtl_bridge_mcast=&ops;
'''
core = (ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/net/core/rtl9617c-bridge.c').read_text()
code += function(core, 'rtl9617c_bridge_mcast_local')
bridge = (args.kernel/'net/bridge/br_input.c').read_text()
for name in ('br_netif_receive_skb','br_pass_frame_up','br_handle_frame_finish'):
    code += function(bridge, name)
code += r'''
int main(void){
 unsigned cases=0;
 for(unsigned broadcast=0;broadcast<2;broadcast++)for(unsigned host=0;host<2;host++)for(unsigned mode=0;mode<10;mode++)for(vid=0;vid<2;vid++) {
  struct net_device input={0},dev={0};struct net_bridge br={.dev=&dev,.vg={true}};dev.priv=&br;
  struct sk_buff s={.dev=&input,.eth={.h_dest={1},.h_source={2}},.len=64};
  if(broadcast)memset(s.eth.h_dest,255,6);
  port=(struct net_bridge_port){.br=&br,.state=BR_STATE_FORWARDING};
  s.frame.multicast_host_group=host?7:0;s.frame.multicast_host_owner=42;
  mdb.host_joined=have_mdb=querier=ingress=filter=true;router=stale=allocation_fail=false;
  delivered=drops=forwarded=flooded=selected=vlan_calls=local_hooks=validated=0;
  if(mode==1)mdb.host_joined=false;
  if(mode==2){mdb.host_joined=false;dev.flags=IFF_PROMISC;}
  if(mode==3){mdb.host_joined=false;dev.flags=IFF_ALLMULTI;}
  if(mode==4)ingress=false;
  if(mode==5)br.vg.allowed=false;
  if(mode==6)filter=false;
  if(mode==7)stale=true;
  if(mode==8)allocation_fail=true;
  if(mode==9){have_mdb=false;querier=false;}
  br_handle_frame_finish(&net,NULL,&s);
  assert(!forwarded);
  if(host) {
   assert(!selected&&!flooded);
   bool pass=broadcast?(mode<4||mode==9):(mode==0||mode==2||mode==3||mode==9);
   assert(delivered==pass&&drops==!pass);
   assert(validated==(mode!=4));
   assert(vlan_calls==(pass||mode==6)&&local_hooks==vlan_calls);
  } else {
   assert(selected==(!broadcast&&mode!=4&&mode!=9)&&flooded==(broadcast?mode!=4:mode==9));
   assert(!validated);
  }
  cases++;
 }
 printf("Linux bridge host copies: %u actual finish/pass-up cases, MDB/promisc/allmulti, ingress/local VLAN, filtering, stale/OOM and ordinary miss forwarding passed\n",cases);
}
'''
run(code, 'bridge')

# Flags can change without a NETDEV_CHANGE notification. Verify publication
# itself closes the profile, without holding an RCU writer across driver ops.
code = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#include "linux/rtl9617c-bridge.h"
#include "rtl9617c-fe-mcast-gate.h"
#define CONFIG_ARCH_RTL9617C 1
#define IFF_PROMISC 256
#define IFF_ALLMULTI 512
#define ASSERT_RTNL() ((void)0)
#define unlikely(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define netdev_warn(...) ((void)0)
#define netdev_info(...) ((void)0)
#define current_uid_gid(a,b) do {*(a)=0;*(b)=0;} while(0)
#define audit_log(...) ((void)0)
typedef unsigned kuid_t,kgid_t;
static bool audit_enabled;
struct net_device {unsigned flags,gflags,promiscuity,allmulti;};
static struct rtl_fe_mc_gate gate;
static unsigned profile=3,begins,ends;
static u64 epoch;
static unsigned read_reg(void *p,unsigned r){(void)p;assert(r==0x372c);return profile;}
static void write_reg(void *p,unsigned r,unsigned v){(void)p;assert(r==0x372c);profile=v;}
static struct rtl_dq_io io={NULL,read_reg,write_reg,NULL};
static struct rtl9617c_bridge_mcast_ops ops;
const struct rtl9617c_bridge_mcast_ops *rtl9617c_bridge_mcast_begin(struct net_device *d){
 assert(!(d->flags&(IFF_PROMISC|IFF_ALLMULTI))||d->promiscuity==0||d->allmulti==0);
 begins++;rtl_fe_mc_gate_begin(&io,&gate);return &ops;
}
void rtl9617c_bridge_mcast_end(const struct rtl9617c_bridge_mcast_ops *o){assert(o==&ops);ends++;rtl_fe_mc_gate_end(&gate);}
static void dev_change_rx_flags(struct net_device *d,unsigned f){(void)d;(void)f;assert(!gate.active&&profile==3&&!gate.writers&&gate.epoch==epoch+2);}
static void netdev_ops_assert_locked(struct net_device *d){(void)d;}
static void __dev_notify_flags(struct net_device *d,unsigned f,unsigned g,unsigned x,void *y){(void)d;(void)f;(void)g;(void)x;(void)y;assert(!gate.writers);}
static void dev_set_rx_mode(struct net_device *d){(void)d;assert(!gate.writers);}
'''
dev = (args.kernel/'net/core/dev.c').read_text()
code += function(dev, '__dev_set_promiscuity')+function(dev, 'netif_set_allmulti')
code += r'''
int main(void){
 for(unsigned kind=0;kind<2;kind++)for(unsigned notify=0;notify<2;notify++) {
  struct net_device d={0};
  for(unsigned step=0;step<4;step++) {
   int inc=step<2?1:-1;unsigned before=begins;epoch=gate.epoch;
   assert(!rtl_fe_mc_gate_open(&io,&gate,epoch));
   int ret=kind?netif_set_allmulti(&d,inc,notify):__dev_set_promiscuity(&d,inc,notify);
   assert(!ret&&begins==ends);
   if(step==0||step==3)assert(begins==before+1&&!gate.active&&gate.epoch==epoch+2);
   else assert(begins==before&&gate.active&&gate.epoch==epoch);
  }
  d.promiscuity=d.allmulti=~0U;epoch=gate.epoch;
  assert((kind?netif_set_allmulti(&d,1,notify):__dev_set_promiscuity(&d,1,notify))==-EOVERFLOW);
  assert(gate.epoch==epoch&&!gate.writers);
 }
 puts("Linux receive flags: actual promisc/allmulti publication, refcounts, notify-off and overflow close/reopen checks passed");
}
'''
run(code, 'flags')
