#!/usr/bin/env python3
"""Execute native complete multicast capture/learn and production FE tables."""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DRIVERS=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
# Reuse the existing faithful table/FE harness without running its main.
setup=(ROOT/'scripts/test_fe_mcast_integration.py').read_text().split("code += r'''\nint main(void)",1)[0]
scope={'__file__':str(ROOT/'scripts/test_fe_mcast_integration.py')}
exec(compile(setup,'multicast-integration-fixture','exec'),scope)
code=scope['code']
code=code.replace('static int fe_mcast_direct_stage(struct rtl_fe *fe,struct rtl_fe_flow *flow,struct rtl_fe_mc_rule *mc){(void)fe;(void)flow;for(unsigned i=0;i<mc->count;i++)assert(!mc->direct[i].wifi_id);return 0;}',
 'static int fe_mcast_direct_stage(struct rtl_fe *fe,struct rtl_fe_flow *flow,struct rtl_fe_mc_rule *mc);')
code=code.replace('static void fe_mcast_host_remove(struct rtl_fe *f,struct rtl_fe_flow *v){(void)f;assert(!v->mc_host);}',
 'static void fe_mcast_host_remove(struct rtl_fe *f,struct rtl_fe_flow *v);')
code=code.replace('static int fe_mcast_host_publish(struct rtl_fe *f,struct rtl_fe_flow *v,struct rtl_fe_mc_host *h){(void)f;(void)v;(void)h;assert(0);return -1;}',
 'static int fe_mcast_host_publish(struct rtl_fe *f,struct rtl_fe_flow *v,struct rtl_fe_mc_host *h);')
code=code.replace('struct net_device {bool running,carrier;};','''struct nf_hook_entries {unsigned num_hook_entries;struct {void (*hook)(void);} hooks[3];};
#define NL80211_IFTYPE_AP 3
#define NL80211_IFTYPE_STATION 2
#define VLAN_FLAG_REORDER_HDR 1
#define NETIF_F_HW_VLAN_CTAG_TX 1
#define NETIF_F_HW_VLAN_STAG_TX 2
struct wireless_dev {unsigned iftype;};
struct vlan_dev_priv {unsigned flags,ingress_priority_map[8],egress_priority_map[8];};
struct net {unsigned refs;struct {struct nf_hook_entries *hooks_bridge[5];} nf;};
struct net_device {bool running,carrier;unsigned ifindex,group,flags,num_tx_queues,mtu,vid,features;u8 dev_addr[6];struct netdev_queue *queue;struct net_device *master,*lower;struct net *net;void *tcx_ingress,*tcx_egress;struct nf_hook_entries *nf_hooks_ingress,*nf_hooks_egress;struct wireless_dev *ieee80211_ptr;struct vlan_dev_priv vlan;};''')
code=code.replace('struct rtl_fe_flow {',re.search(r'struct rtl_fe_mc_host \{.*?\n\};',(DRIVERS/'rtl9617c-fe.c').read_text(),re.S)[0]+'\nstruct rtl_fe_flow {')
code=code.replace('struct rtl_fe {','#include "rtl9617c-epon-path.h"\nstruct rtl_fe_mtu {unsigned users,size,saved;};\nstruct rtl_fe {struct rtl_epon_paths epon_paths;bool epon_admitted;struct net_device *itu_netdev;struct rtl_itu_paths *itu_paths;struct rtl_fe_mtu mtus[16];struct rtl_fe_mc_host *mc_hosts[256];struct rtl_fe_mc_direct *mc_directs[256];')
code=code.replace('struct rtl_fe_mc_profile mc_profile;','''struct rtl_fe_mc_profile mc_profile;
 struct {struct rtl_mc_pe_table *table;struct net_device *netdev;} mc_pe[2];
 struct rtl_fe_mc_gate mc_gate;int lock,mc_lock;struct rtl_fe_port *ports[6];''')
code=code.replace('#include "rtl9617c-fe-mcast-profile.h"','#include "rtl9617c-fe-mcast-profile.h"\n#include "rtl9617c-fe-mcast-gate.h"')
code=code.replace('typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32,__le32;typedef uint64_t u64;',
 'typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32,__le32;typedef uint64_t u64;\n#include "linux/rtl9617c-bridge.h"')
code=code.replace('assert(!fe->indexed);fe->indexed=flow;','fe->indexed=flow;')
code=code.replace('return fe->indexed && fe->indexed->cookie==cookie?fe->indexed:NULL;',
 'for(struct list_head *p=fe->flows.next;p!=&fe->flows;p=p->next){struct rtl_fe_flow *f=container_of(p,struct rtl_fe_flow,list);if(f->cookie==cookie)return f;}return NULL;')
code=code.replace('(void)n;(*root)++;','struct rtl_fe *fe=container_of(root,struct rtl_fe,cookies);if(fe->indexed && &fe->indexed->cookie_node==n)fe->indexed=NULL;(*root)++;')
code=code.replace('static int fe_mtu_get(struct rtl_fe *f,struct rtl_fe_flow *v,unsigned n){(void)f;(void)v;(void)n;assert(0);return -1;}',
 'static unsigned fe_read(struct rtl_fe *f,unsigned r){return read_reg(f,r);}'
 +'static void fe_write(struct rtl_fe *f,unsigned r,unsigned v){write_reg(f,r,v);}'
 +scope['function']('fe_mtu_get'))
code=code.replace('static void fe_mtu_put(struct rtl_fe *f,struct rtl_fe_flow *v){(void)f;(void)v;assert(0);}',scope['function']('fe_mtu_put'))
code=code.replace('static struct rtl_fe_wifi_peer *fe_wifi_peer(struct rtl_fe *f,struct rtl_fe_port *p,const u8 *a){(void)f;(void)p;(void)a;return NULL;}',
 'static struct rtl_fe_wifi_peer test_peer;static struct rtl_fe_port *test_peer_port;static u64 test_policy_generation=1;'
 +'u64 rtl9617c_bridge_wifi_policy_generation(void){return test_policy_generation;}'
 +'static struct rtl_fe_wifi_peer *fe_wifi_peer(struct rtl_fe *f,struct rtl_fe_port *p,const u8 *a){(void)f;(void)a;return p==test_peer_port?&test_peer:NULL;}')
bridge=(DRIVERS/'rtl9617c-fe-bridge.h').read_text()
code+=re.search(r'struct fe_mcast_packet \{.*?\n\};',bridge,re.S)[0]+'\n'
code+=r'''
#define IFF_PROMISC 256
#define IFF_ALLMULTI 512
#define rtnl_dereference(p) (p)
struct Qdisc_ops {const char *id;};
struct Qdisc {struct Qdisc_ops *ops;};
struct netdev_queue {struct Qdisc *qdisc_sleeping;};
static struct netdev_queue *netdev_get_tx_queue(struct net_device *d,unsigned q){return d->queue+q;}
#define CONFIG_NETFILTER_FAMILY_BRIDGE 1
#define CONFIG_NET_XGRESS 1
#define CONFIG_NETFILTER_INGRESS 1
#define CONFIG_NETFILTER_EGRESS 1
#define NF_INET_NUMHOOKS 5
#define rcu_dereference(p) (p)
#define rcu_access_pointer(p) (p)
static void rcu_read_lock(void){}
static void rcu_read_unlock(void){}
static void fe_bridge_capture(void){}
static void fe_bridge_forwarded(void){}
static void fe_bridge_wire(void){}
static void rtl_wifi_ingress(void){}
#define RTL_WIFI_SOURCE 18
#define RTL_WIFI_ID_MAX 60
static void foreign_hook(void){}
#include "linux/rtl9617c-qm-layout.h"
#define FE_MCAST_PENDING 16
#define ETH_HLEN 14
#define ETH_P_IP 0x800
#define ETH_P_IPV6 0x86dd
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define ETH_P_PPP_SES 0x8864
#include "rtl9617c-pon-path.h"
#define VLAN_VID_MASK 4095
#define VLAN_HLEN 4
#define BR_STATE_FORWARDING 3
#define IPPROTO_UDP 17
#define SKB_EXT_RTL9617C_BRIDGE 0
#define GFP_ATOMIC 0
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,n) ((v)=(n))
#define list_last_entry(h,t,m) container_of((h)->prev,t,m)
#define spin_lock_irqsave(p,f) do {assert(!*(p));*(p)=1;(f)=0;} while(0)
#define spin_unlock_irqrestore(p,f) do {assert(*(p));*(p)=0;(void)(f);} while(0)
struct rtl_fe_bridge {struct rtl_fe *fe;bool stopping;int epoch;unsigned work;struct fe_mcast_packet *multicast_pending[FE_MCAST_PENDING];};
struct sk_buff {struct net_device *dev;unsigned priority;bool gso,rtl9617c_fttr_ingress;struct rtl9617c_bridge_frame saved,frame;};
#define eth_hdr(s) (&(s)->frame)
#define h_dest destination
static int cow_failure;
static int skb_cow_head(struct sk_buff *s,unsigned n){(void)s;assert(!n);return cow_failure;}
static u64 fe_mcast_ticket;
static int atomic_read(int *p){return *p;}
static bool rtl_fe_enabled(struct rtl_fe *f){return f->enabled;}
static bool skb_is_gso(struct sk_buff *s){return s->gso;}
static struct rtl_fe *ext_change;
static void fe_mcast_invalidate(struct rtl_fe *fe);
static struct rtl9617c_bridge_frame *skb_ext_find(struct sk_buff *s,int id){assert(!id);return &s->saved;}
static struct rtl9617c_bridge_frame *skb_ext_add(struct sk_buff *s,int id){assert(!id);if(ext_change){fe_mcast_invalidate(ext_change);ext_change=NULL;}return &s->saved;}
static bool is_multicast_ether_addr(const u8 *a){return a[0]&1;}
static bool is_valid_ether_addr(const u8 *a){return !(a[0]&1)&&memcmp(a,"\0\0\0\0\0\0",6);}
static bool is_broadcast_ether_addr(const u8 *a){return !memcmp(a,"\xff\xff\xff\xff\xff\xff",6);}
static bool is_link_local_ether_addr(const u8 *a){return !memcmp(a,"\1\x80\xc2\0\0",5)&&a[5]<16;}
static bool ether_addr_equal(const u8 *a,const u8 *b){return !memcmp(a,b,6);}
static struct net_device *devices[16];
static unsigned vlan_dev_vlan_id(struct net_device *d){return d->vid;}
static struct vlan_dev_priv *vlan_dev_priv(struct net_device *d){return &d->vlan;}
static unsigned short vlan_dev_vlan_proto(struct net_device *d){(void)d;return __builtin_bswap16(0x8100);}
static unsigned vlan_dev_get_egress_qos_mask(struct net_device *d,unsigned p){return d->vlan.egress_priority_map[p&7];}
static bool netif_is_bridge_port(struct net_device *d){return d->master!=NULL;}
static struct net_device *__vlan_find_dev_deep_rcu(struct net_device *d,unsigned short protocol,unsigned vid){
 for(unsigned i=0;i<16;i++)if(devices[i]&&devices[i]->lower==d && protocol==__builtin_bswap16(0x8100) && devices[i]->vid==vid)return devices[i];
 return NULL;
}
static bool is_vlan_dev(struct net_device *dev){return dev->lower!=NULL;}
static struct net_device *vlan_dev_real_dev(struct net_device *dev){return dev->lower;}
static struct net_device *netdev_master_upper_dev_get_rcu(struct net_device *d){return d->master;}
static struct net_device *netdev_master_upper_dev_get(struct net_device *d){return d->master;}
static bool netif_is_bridge_master(struct net_device *d){return d==devices[10];}
static struct net *dev_net(struct net_device *d){return d->net;}
static struct net *get_net(struct net *n){n->refs++;return n;}
static void put_net(struct net *n){assert(n->refs);n->refs--;}
static struct net_device *__dev_get_by_index(struct net *n,unsigned i){(void)n;return i<16?devices[i]:NULL;}
static unsigned br_port_get_stp_state(struct net_device *d){return d->running?3:0;}
static struct net_device *fdb_source;
static struct net_device *br_fdb_find_port(struct net_device *b,const u8 *m,unsigned vid){(void)b;(void)m;(void)vid;return fdb_source?fdb_source:devices[1];}
static struct rtl_fe_port *fe_port(struct rtl_fe *fe,struct net_device *d){for(unsigned i=0;i<6;i++)if(fe->ports[i]&&fe->ports[i]->netdev==d)return fe->ports[i];return NULL;}
static int fe_vlan_tpid(unsigned short p){return p==__builtin_bswap16(0x8100)?1:-EOPNOTSUPP;}
#define htons(v) __builtin_bswap16(v)
#define ntohs(v) __builtin_bswap16(v)
static unsigned get_unaligned_be32(const void *p){unsigned v;memcpy(&v,p,4);return __builtin_bswap32(v);}
static void skb_reset_mac_header(struct sk_buff *s){(void)s;}
#define dev_get_by_index_rcu __dev_get_by_index
static void __skb_pull(struct sk_buff *s,unsigned n){(void)s;assert(n==14);}
static void __skb_push(struct sk_buff *s,unsigned n){(void)s;assert(n==14);}
/* Wire/VLAN parsing has its own native packet regression. This harness feeds
 * its complete frame result through real transaction callbacks and FE code. */
static int fe_bridge_frame(struct sk_buff *s,struct rtl9617c_bridge_frame *f){*f=s->frame;return 0;}
static struct net_device *fe_bridge_physical(struct net_device *d,struct rtl9617c_bridge_frame *f,unsigned p){(void)f;(void)p;return d;}
'''
# Capture completions queue immediately, whereas FE retry waits HZ.
code=code.replace('assert(q==0 && d==HZ);','assert(q==0 && (d==HZ || !d));')
code+=r'''
static void fe_mcast_pe_close(struct rtl_fe *f){for(unsigned i=0;i<2;i++)if(f->mc_pe[i].table)f->mc_pe[i].table->epoch=0;}
static void fe_mcast_invalidate(struct rtl_fe *fe){struct rtl_dq_io io=fe_io(fe);fe_mcast_pe_close(fe);rtl_fe_mc_gate_begin(&io,&fe->mc_gate);rtl_fe_mc_gate_end(&fe->mc_gate);}
'''
code+=(DRIVERS/'rtl9617c-fe-vlan.h').read_text().split('static int fe_evto_upstream',1)[0]
code+=(DRIVERS/'rtl9617c-fe-mcast-learn.h').read_text()
code+=r'''
#define kvmalloc(n,f) malloc(n)
#define kvfree free
static void mutex_lock(int *v){assert(!*v);*v=1;}
static void mutex_unlock(int *v){assert(*v);*v=0;}
static void fe_bridge_invalidate(struct rtl_fe *f){fe_mcast_invalidate(f);}
static void fe_collect_ports(struct rtl_fe *f){(void)f;}
static void fe_wifi_forget_port(struct rtl_fe *f,struct rtl_fe_port *p){(void)f;(void)p;}
static void fe_mcast_pe_forget_port(struct rtl_fe *f,struct net_device *p){(void)f;(void)p;}
'''
for name in ('fe_flow_pon','fe_pon_quiesce_locked','rtl_fe_set_itu_paths','rtl_fe_set_epon_paths','rtl_fe_flush_port'):
    code+=scope['function'](name)
code+=r'''
int main(void){
 static struct rtl_fe fe;struct net net={0};
 struct net_device br={.ifindex=10,.mtu=1500,.running=true,.carrier=true,.net=&net};
 struct net_device lan={.ifindex=1,.mtu=1500,.running=true,.carrier=true,.master=&br,.net=&net};
 struct net_device out={.ifindex=2,.mtu=1400,.running=true,.carrier=true,.master=&br,.net=&net};
 struct net_device p6={.ifindex=6,.mtu=1982,.running=true,.carrier=true,.net=&net};
 struct net_device shared={.ifindex=7,.mtu=1500,.running=true,.carrier=true,.master=&br,.net=&net};
 struct rtl_fe_port input={.fe=&fe,.netdev=&lan},output={.fe=&fe,.netdev=&out,.source=1,.destination=1},fttr={.fe=&fe,.netdev=&p6,.source=6,.destination=6};
 devices[1]=&lan;devices[2]=&out;devices[6]=&p6;devices[7]=&shared;devices[10]=&br;
 fe.ports[0]=&input;fe.ports[1]=&output;fe.ports[2]=&fttr;
 fe.enabled=fe.fttr_admitted=true;fe.fttr_netdev=&p6;fe.fttr_mcast=&shared;
 fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);
 fe.reg[0x351c/4]=0x80101002;
 fe.reg[0xaa60/4]=0x2f0ff;fe.reg[0xa1c0/4]=0x24009b;fe.reg[0x1600/4]=0x80;
 fe.map[16]=fe.map[144]=9;fe.map[1]=fe.map[129]=1;fe.map[70]=fe.map[198]=8;
 for(unsigned i=0;i<4;i++)fe.reg[(0x1704+i*4)/4]=0x8100;
 struct rtl_fe_bridge bridge={.fe=&fe};struct sk_buff skb={.dev=&lan};
 skb.saved=(struct rtl9617c_bridge_frame){.ingress=1,.port=1,.protocol=ETH_P_IP,.ip_protocol=17,.ip_source={0x0a000001},.ip_destination={0xef010203}};
 memcpy(skb.saved.source,"\2\0\0\0\0\1",6);memcpy(skb.saved.destination,"\1\0\x5e\1\2\3",6);skb.frame=skb.saved;
 struct nf_hook_entries ours={.num_hook_entries=2,.hooks={{fe_bridge_capture},{fe_bridge_forwarded}}};
 struct nf_hook_entries wire={.num_hook_entries=1,.hooks={{fe_bridge_wire}}};
 struct nf_hook_entries foreign={.num_hook_entries=1,.hooks={{foreign_hook}}};
 net.nf.hooks_bridge[0]=&ours;lan.nf_hooks_ingress=&wire;
 assert(fe_mcast_hooks_clear(&net)&&fe_mcast_port_clear(&lan));
 /* Reserved bridge control MACs can depend on group-forward masks before
  * flood selection and must never acquire a learned data bypass. */
 memcpy(skb.saved.destination,"\1\x80\xc2\0\0\0",6);
 assert(!fe_mcast_capture_begin(&bridge,&skb,true,++fe_mcast_ticket)&&!net.refs);
 skb.saved=skb.frame;
 net.nf.hooks_bridge[1]=&foreign;
 assert(!fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket)&&!net.refs);
 net.nf.hooks_bridge[1]=NULL;
 struct Qdisc_ops normal_ops={"fq_codel"},policy_ops={"htb"};
 struct Qdisc normal={&normal_ops},policy={&policy_ops};struct netdev_queue queue[2]={{&normal},{&normal}};
 out.num_tx_queues=2;out.queue=queue;assert(fe_mcast_port_clear(&out));
 struct net_device vlan={.lower=&out};assert(fe_mcast_path_clear(&vlan));
 queue[1].qdisc_sleeping=&policy;assert(!fe_mcast_path_clear(&vlan));queue[1].qdisc_sleeping=&normal;
 for(unsigned filter=0;filter<5;filter++) {
  if(filter==0)out.tcx_ingress=&out;
  if(filter==1)out.tcx_egress=&out;
  if(filter==2)out.nf_hooks_ingress=&foreign;
  if(filter==3)out.nf_hooks_egress=&foreign;
  if(filter==4)queue[1].qdisc_sleeping=&policy;
  assert(!fe_mcast_port_clear(&out));
  skb.dev=&lan;u64 ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket);
  fe_mcast_capture_select(&bridge,&skb,&out);skb.dev=&out;fe_mcast_capture_output(&bridge,&skb);
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(!fe.indexed&&!net.refs&&!fe.mc_gate.active);
  out.tcx_ingress=out.tcx_egress=NULL;out.nf_hooks_ingress=out.nf_hooks_egress=NULL;queue[1].qdisc_sleeping=&normal;
 }
 for(unsigned scenario=0;scenario<6;scenario++) {
  skb.dev=&lan;skb.saved.multicast_epoch=fe.mc_gate.epoch;skb.saved.epoch=bridge.epoch;
  u64 ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket&&net.refs==1);
  fe_mcast_capture_select(&bridge,&skb,&out);fe_mcast_capture_select(&bridge,&skb,&shared);
  skb.dev=&out;fe_mcast_capture_output(&bridge,&skb);
  if(scenario!=0){skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);}
  if(scenario==1)fe_mcast_capture_output(&bridge,&skb);
  if(scenario==2){struct rtl_dq_io io=fe_io(&fe);rtl_fe_mc_gate_begin(&io,&fe.mc_gate);rtl_fe_mc_gate_end(&fe.mc_gate);}
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(!net.refs);
  if(scenario<3)assert(!fe.indexed&&!fe.mc_gate.active);
  else {
   assert(fe.indexed&&fe.indexed->learned&&fe.indexed->replica_count==2&&fe.mc_gate.active&&fe.reg[0x372c/4]==4);
   assert(fe.indexed->mc_epoch==fe.mc_gate.epoch);
   assert(fe.indexed->mtu_owned&&fe.mtus[fe.indexed->mtu_index].size==1400);
   assert((fe.reg[(0x3520+fe.indexed->mtu_index/2*4)/4]>>(fe.indexed->mtu_index%2*16)&0x3fff)==1400);
  }
 }
 /* Real gate closure prevents the old generation from being republished;
  * next complete packet retires it before making a new group visible. */
 unsigned long installed=fe.installs;struct rtl_dq_io io=fe_io(&fe);
 /* Same epoch/key but a different full replica recipe must close lookup. */
 skb.dev=&lan;skb.saved.multicast_epoch=fe.mc_gate.epoch;
 u64 drift=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(drift);
 fe_mcast_capture_select(&bridge,&skb,&shared);skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);
 fe_mcast_capture_end(&bridge,drift,true);fe_mcast_process(&bridge);
 assert(!fe.mc_gate.active&&fe.reg[0x372c/4]==3);
 skb.dev=&lan;skb.saved.multicast_epoch=fe.mc_gate.epoch;
 u64 ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket);
 fe_mcast_capture_select(&bridge,&skb,&shared);skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);
 fe_mcast_capture_end(&bridge,ticket,true);fe.event_count=0;fe_mcast_process(&bridge);
 assert(fe.indexed&&fe.indexed->replica_count==1&&fe.installs==installed+1&&fe.mc_gate.active);
 assert(fe.mtus[fe.indexed->mtu_index].size==1500);
 rtl_fe_mc_gate_close(&io,&fe.mc_gate);assert(!fe_delete(&fe,fe.indexed));
 /* A failed install whose rollback also fails remains learned/deleting,
  * and cannot be bypassed by later publication of a different generation. */
 skb.dev=&lan;skb.saved.multicast_epoch=fe.mc_gate.epoch;
 ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket);
 fe_mcast_capture_select(&bridge,&skb,&shared);skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);
 fe_mcast_capture_end(&bridge,ticket,true);fe.event_count=0;fe.age_fail=fe.ni_fail=1;
 fe_mcast_process(&bridge);
 assert(fe.indexed&&fe.indexed->learned&&fe.indexed->deleting&&!fe.mc_gate.active);
 skb.dev=&lan;skb.saved.multicast_epoch=fe.mc_gate.epoch;
 ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket);
 fe_mcast_capture_select(&bridge,&skb,&shared);skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);
 fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
 assert(fe.indexed&&fe.indexed->learned&&!fe.indexed->deleting&&fe.mc_gate.active);
 rtl_fe_mc_gate_close(&io,&fe.mc_gate);assert(!fe_delete(&fe,fe.indexed));
 /* Full host+LAN/shared transaction, source guard, late generation and
  * host-only groups exercise the actual table/lookup/withdrawal functions. */
 for(unsigned local_only=0;local_only<2;local_only++) {
  fe.event_count=0;skb.dev=&lan;skb.saved.multicast_epoch=fe.mc_gate.epoch;
  ticket=fe_mcast_capture_begin(&bridge,&skb,true,++fe_mcast_ticket);assert(ticket);
  if(!local_only){fe_mcast_capture_select(&bridge,&skb,&shared);skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);}
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed&&fe.indexed->mc_host&&fe.mc_gate.active&&net.refs==1);
  unsigned group=fe.indexed->mc_group,action=64;
  assert(fe.indexed->replica_count==2-local_only);
  for(unsigned a=0;a<64;a++)if(fe.multicast.groups[group].bitmap[a/32]&(1U<<(a%32)))
   if((fe.actions[a][1]>>24&63)==16)action=a;
  assert(action<64&&fe.actions[action][0]==(256U+group)<<23&&fe.actions[action][2]==0x801);
  struct sk_buff copy={.frame=skb.frame};
  assert(fe_mcast_host_receive(&bridge,&copy,group,1)==-ESTALE);
  assert(!fe_mcast_host_receive(&bridge,&copy,group,0)&&copy.dev==&lan);
  assert(copy.saved.multicast_host_group==group&&copy.saved.multicast_host_owner==ticket);
  assert(fe_mcast_host_local(&bridge,&copy,&br,0)==1);
  assert(!fe_mcast_capture_begin(&bridge,&copy,true,++fe_mcast_ticket));
  copy.dev=&out;assert(fe_mcast_host_local(&bridge,&copy,&br,0)==-ESTALE);copy.dev=&lan;
  assert(fe_mcast_host_local(&bridge,&copy,&br,1)==-ESTALE);
  copy.frame.destination_port++;assert(fe_mcast_host_receive(&bridge,&copy,group,0)==-ESTALE);copy.frame.destination_port--;
  fe_mcast_invalidate(&fe);
  assert(fe_mcast_host_local(&bridge,&copy,&br,0)==-ESTALE);
  assert(fe_mcast_host_receive(&bridge,&copy,group,0)==-ESTALE);
  /* Even a failed Hash withdrawal revokes host delivery immediately. */
  fe.age_fail=1;assert(fe_delete(&fe,fe.indexed)==-ETIMEDOUT&&!fe.mc_hosts[group]&&!net.refs);
  assert(fe_mcast_host_local(&bridge,&copy,&br,0)==-ESTALE);
  assert(!fe_delete(&fe,fe.indexed));assert(fe.multicast.groups[group].state==RTL_FE_MC_RETIRED);
 }
 /* ONU source6 enters on a specific GEM VLAN; LAN/CPU preserve the
  * source, only shared P6 replica rewrites it without decrementing TTL. */
 struct net_device onu={.ifindex=8,.mtu=1500,.vid=100,.lower=&p6,.master=&br,.net=&net,.running=true,.carrier=true};
 devices[8]=&onu;fdb_source=&onu;
 memcpy(shared.dev_addr,"\2\x22\x33\x44\x55\x66",6);
 for(unsigned local=0;local<2;local++) {
  out.mtu=shared.mtu=br.mtu=1982;
  fe.event_count=0;skb.dev=&onu;
  skb.saved.ingress=6;skb.saved.port=8;skb.saved.tags=1;skb.saved.tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=100};
  skb.saved.multicast_epoch=fe.mc_gate.epoch;skb.saved.multicast_host_group=0;
  ticket=fe_mcast_capture_begin(&bridge,&skb,local,++fe_mcast_ticket);assert(ticket);
  fe_mcast_capture_select(&bridge,&skb,&out);fe_mcast_capture_select(&bridge,&skb,&shared);
  skb.frame.tags=0;memset(skb.frame.tag,0,sizeof(skb.frame.tag));
  skb.dev=&out;fe_mcast_capture_output(&bridge,&skb);
  skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed && fe.indexed->input==&fttr && fe.indexed->replica_count==2+local && fe.mc_gate.active);
  assert(fe.mtus[fe.indexed->mtu_index].size==(local?1566:1978));
  unsigned group=fe.indexed->mc_group;bool p6edit=false,lanedit=false;
  for(unsigned a=0;a<64;a++)if(fe.multicast.groups[group].bitmap[a/32]&(1U<<(a%32))) {
   unsigned dest=fe.actions[a][1]>>24&63;
   if(dest==6){assert(fe.actions[a][2]==3 && (fe.actions[a][1]>>2&4095)==4095);p6edit=true;}
   if(dest==1){assert(fe.actions[a][2]==1);lanedit=true;}
  }
  assert(p6edit&&lanedit&&fe.multicast.macs[0].exposed);
  assert(fe.reg[0x1714/4]==0x33445566 && (fe.reg[0x1718/4]&65535)==0x222);
  if(local) {
   struct sk_buff copy={.frame=skb.saved};
   assert(!fe_mcast_host_receive(&bridge,&copy,group,6)&&copy.dev==&p6&&copy.rtl9617c_fttr_ingress);
   copy.dev=&onu;assert(fe_mcast_host_local(&bridge,&copy,&br,0)==1);
   fe.fttr_admitted=false;
   assert(fe_mcast_host_receive(&bridge,&copy,group,6)==-ESTALE);
   assert(fe_mcast_host_local(&bridge,&copy,&br,0)==-ESTALE);fe.fttr_admitted=true;
  }
  /* Same exact decision must reuse the flow, not consume a second MAC/group. */
  struct fe_mcast_packet again={.net=&net,.bridge=10,.epoch=fe.mc_gate.epoch,.input=skb.saved,.local=local,.count=2};
  again.output[0].port=again.output[0].physical=2;again.output[0].frame=skb.frame;
  again.output[1].port=again.output[1].physical=7;again.output[1].frame=skb.frame;
  assert(!fe_mcast_learn(&bridge,&again)&&fe.indexed->mc_group==group);
  /* Exact ingress GEM, source admission and shared MAC changes matter. */
  again.input.tag[0].tci=101;assert(fe_mcast_learn(&bridge,&again)==-EOPNOTSUPP);again.input.tag[0].tci=100;
  fe.fttr_admitted=false;assert(fe_mcast_learn(&bridge,&again)==-EOPNOTSUPP);fe.fttr_admitted=true;
  shared.dev_addr[5]++;assert(fe_mcast_learn(&bridge,&again)==-ESTALE&&!fe.mc_gate.active);shared.dev_addr[5]--;
  assert(!fe_delete(&fe,fe.indexed));
  assert(fe.multicast.macs[0].exposed && !fe.multicast.macs[0].users);
 }
 /* ONU/LAN to PON replica compiles each wire GEM/T-CONT/COS separately. */
 struct net_device pon={.ifindex=9,.mtu=1500,.running=true,.carrier=true,.master=&br,.net=&net};
 struct rtl_fe_port pp={.fe=&fe,.netdev=&pon,.source=7,.destination=32};
 devices[9]=&pon;fe.ports[3]=&pp;fe.itu_netdev=&pon;fe.pon_requested=true;
 struct rtl_itu_paths paths={.count=1,.generation=7,.path={{.index=55,.gem_id=1000,.tcont=5,.queue=3,.pbits=255,.vlan=RTL_ITU_VLAN_ANY}}};
 for(unsigned s=0;s<RTL_EVTO_STAGES;s++)paths.path[0].vlan_profile[s]=RTL_EVTO_NONE;
 fe.itu_paths=&paths;fdb_source=&lan;
 for(unsigned i=0;i<4;i++)fe.map[37+i*64]=8;
 fe.reg[0x68f8/4]=10;
 for(unsigned tcont=5;tcont<=31;tcont+=26) {
  paths.path[0].tcont=tcont;
  for(unsigned i=0;i<4;i++)fe.map[32+tcont+i*64]=8;
  for(unsigned local=0;local<2;local++) {
   fe.event_count=0;skb.dev=&lan;skb.saved.ingress=skb.saved.port=1;skb.saved.tags=0;
   memset(skb.saved.tag,0,sizeof(skb.saved.tag));skb.saved.multicast_epoch=fe.mc_gate.epoch;
   ticket=fe_mcast_capture_begin(&bridge,&skb,local,++fe_mcast_ticket);assert(ticket);
   fe_mcast_capture_select(&bridge,&skb,&pon);fe_mcast_capture_select(&bridge,&skb,&shared);
   skb.dev=&pon;fe_mcast_capture_output(&bridge,&skb);skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);
   fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
   assert(fe.indexed&&fe.indexed->replica_count==2+local&&fe.mc_gate.active);
   unsigned group=fe.indexed->mc_group,pon_actions=0;
   for(unsigned a=0;a<64;a++)if(fe.multicast.groups[group].bitmap[a/32]&(1U<<(a%32))) {
    if((fe.actions[a][1]>>24&63)==32+tcont) {
     assert((fe.actions[a][0]>>23)==55 && !(fe.actions[a][1]&(1U<<30)) && fe.actions[a][2]==97);pon_actions++;
    }
   }
   assert(pon_actions==1 && (fe.mc_route.ports&(1ULL<<(32+tcont))));
   assert(fe.indexed->input->source!=7 && fe.indexed->output->source!=7 && fe_flow_pon(fe.indexed));
   struct fe_mcast_packet again={.net=&net,.bridge=10,.epoch=fe.mc_gate.epoch,.input=skb.saved,.local=local,.count=2};
   again.output[0].port=again.output[0].physical=9;again.output[0].frame=skb.frame;
   again.output[1].port=again.output[1].physical=7;again.output[1].frame=skb.frame;
   assert(!fe_mcast_learn(&bridge,&again)&&fe.indexed->mc_group==group);
   if(!local) {
    paths.path[0].queue=4;assert(fe_mcast_learn(&bridge,&again)==-ESTALE&&!fe.mc_gate.active);paths.path[0].queue=3;
    fe.age_fail=1;assert(fe_pon_quiesce_locked(&fe)==-ETIMEDOUT&&fe.indexed->deleting&&!fe.mc_gate.active);
    assert(!fe_pon_quiesce_locked(&fe)&&!fe.indexed);
   } else {
    /* Snapshot replacement removes every PON replica before releasing old
     * GEMs, including a PON recipient absent from input/terminal output. */
    fe.itu_paths=kmemdup(&paths,sizeof(paths),0);
    struct rtl_itu_paths *old=fe.itu_paths;
    fe.age_fail=1;assert(rtl_fe_set_itu_paths(&fe,&pon,&paths)==-ETIMEDOUT);
    assert(fe.itu_paths==old&&fe.indexed->deleting&&!fe.mc_gate.active&&!net.refs);
    assert(!rtl_fe_set_itu_paths(&fe,&pon,&paths)&&!fe.indexed&&fe.itu_paths!=old);
    free(fe.itu_paths);fe.itu_paths=&paths;
   }
  }
 }
 /* CPU PON final Ethernet budget includes all post-EVTO tags. */
 pon.mtu=2000;
 for(unsigned tags=0;tags<3;tags++) {
  fe.event_count=0;skb.dev=&lan;skb.saved.multicast_epoch=fe.mc_gate.epoch;
  ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket);
  fe_mcast_capture_select(&bridge,&skb,&pon);skb.dev=&pon;skb.frame.tags=tags;
  for(unsigned i=0;i<tags;i++)skb.frame.tag[i]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=100+i};
  fe_mcast_capture_output(&bridge,&skb);fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed && fe.mtus[fe.indexed->mtu_index].size==1986-tags*4);
  fe_mcast_invalidate(&fe);assert(!fe_delete(&fe,fe.indexed));
 }
 skb.frame.tags=0;memset(skb.frame.tag,0,sizeof(skb.frame.tag));pon.mtu=1500;
 /* Ambiguous and downstream-only paths cannot authorize an upstream copy. */
 struct rtl_fe_mc_replica replica={};
 paths.path[0].multicast=1;assert(fe_mcast_pon_replica(&fe,&pon,&skb.frame,&replica)==-EOPNOTSUPP);
 paths.path[0].multicast=0;paths.count=2;paths.path[1]=paths.path[0];paths.path[1].index=56;
 assert(fe_mcast_pon_replica(&fe,&pon,&skb.frame,&replica)==-EOPNOTSUPP);paths.count=1;
 fe.pon_requested=false;assert(fe_mcast_pon_replica(&fe,&pon,&skb.frame,&replica)==-EOPNOTSUPP);fe.pon_requested=true;
 /* A real VLAN upper skips UNI-stage treatment exactly as CPU TX does. */
 struct net_device pvlan={.ifindex=11,.vid=41,.lower=&pon};devices[11]=&pvlan;
 struct rtl_itu_paths *service=calloc(1,sizeof(*service)+sizeof(service->profile[0]));assert(service);
 service->count=service->profile_count=1;service->path[0]=paths.path[0];service->path[0].vlan=41;
 service->path[0].vlan_profile[0]=0;
 service->profile[0]=(struct rtl_evto_profile){.input_tpid=0x8100,.output_tpid=0x8100,.downstream=8,.count=1};
 unsigned int drop_tagged[4]={0xf8000000,0xe8000000,0xc00f0000,0x000f0000};
 for(unsigned i=0;i<4;i++)for(unsigned b=0;b<4;b++)service->profile[0].row[0][i*4+b]=drop_tagged[i]>>(24-b*8);
 fe.itu_paths=service;struct rtl9617c_bridge_frame tagged=skb.frame;
 tagged.tags=1;tagged.tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=41};
 assert(!fe_mcast_pon_replica(&fe,&pon,&tagged,&replica)&&replica.gem==55&&replica.tags==1);
 devices[11]=NULL;assert(fe_mcast_pon_replica(&fe,&pon,&tagged,&replica)==-EOPNOTSUPP);
 free(service);fe.itu_paths=&paths;
 /* Untagged priority/DSCP cannot silently select different GEMs/queues. */
 paths.count=2;paths.path[0].pbits=1;paths.path[1]=paths.path[0];paths.path[1].pbits=254;paths.path[1].index=56;
 assert(fe_mcast_pon_replica(&fe,&pon,&skb.frame,&replica)==-EOPNOTSUPP);
 paths.count=1;paths.path[0].pbits=255;
 /* Two distinct DSCP-selected rows route to different GEM indices. */
 paths.count=2;paths.path[0].pbits=1;paths.path[0].unmarked_mode=1;
 memset(paths.path[0].mapper_dscp,0,sizeof(paths.path[0].mapper_dscp));paths.path[0].mapper_dscp[0]=1;
 paths.path[1]=paths.path[0];paths.path[1].index=56;paths.path[1].pbits=254;
 assert(fe_mcast_pon_replica(&fe,&pon,&skb.frame,&replica)==-EOPNOTSUPP);
 paths.count=1;paths.path[0].pbits=255;paths.path[0].unmarked_mode=0;
 /* Same wire tuple on different PON GEMs must have different FE keys. */
 paths.count=2;paths.path[0].vlan=RTL_ITU_VLAN_ANY;paths.path[0].downstream_pbits=255;
 paths.path[1]=paths.path[0];paths.path[1].index=56;fdb_source=&pon;
 unsigned old_hash=0;struct rtl_fe_flow *first_gem=NULL;
 for(unsigned gem=55;gem<=56;gem++) {
  fe.event_count=0;skb.dev=&pon;skb.saved.ingress=skb.saved.port=9;
  skb.saved.pon_gem=gem;skb.saved.pon_generation=paths.generation;skb.saved.tags=0;skb.saved.pon_tags=0;
  skb.saved.pon_priority=0;skb.saved.multicast_epoch=fe.mc_gate.epoch;
  ticket=fe_mcast_capture_begin(&bridge,&skb,true,++fe_mcast_ticket);assert(ticket);
  fe_mcast_capture_select(&bridge,&skb,&out);fe_mcast_capture_select(&bridge,&skb,&shared);
  skb.dev=&out;fe_mcast_capture_output(&bridge,&skb);skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed&&fe.indexed->input==&pp&&fe.indexed->ingress_pol==gem&&fe.mc_gate.active);
  unsigned hash=((unsigned *)fe.memory)[fe.indexed->hash_index];
  if(gem==55)old_hash=hash;else assert(old_hash!=hash);
  struct sk_buff copy={.frame=skb.frame};unsigned group=fe.indexed->mc_group;
  assert(!fe_mcast_host_receive(&bridge,&copy,group,7)&&copy.dev==&pon);
  assert(copy.saved.pon_gem==gem&&copy.saved.pon_generation==paths.generation);
  assert(fe_mcast_host_local(&bridge,&copy,&br,0)==1);
  fe.pon_requested=false;assert(fe_mcast_host_receive(&bridge,&copy,group,7)==-ESTALE);fe.pon_requested=true;
  skb.saved.pon_generation++;assert(fe_mcast_pon_input(&fe,&skb.saved)==-ESTALE);skb.saved.pon_generation--;
  skb.saved.pon_gem=54;assert(fe_mcast_pon_input(&fe,&skb.saved)==-EOPNOTSUPP);skb.saved.pon_gem=gem;
  if(gem==55){first_gem=fe.indexed;assert(net.refs==1);}
  else {
   assert(fe.indexed!=first_gem && first_gem->ingress_pol==55 && net.refs==2);
   fe_mcast_invalidate(&fe);assert(!fe_delete(&fe,fe.indexed));assert(!fe_delete(&fe,first_gem));
   assert(fe_mcast_host_local(&bridge,&copy,&br,0)==-ESTALE);
  }
 }
 /* PON input compiler uses both post-OMCI tags and actual VLAN service
  * lookup; wire tags are not the tags seen by the Linux bridge. */
 struct rtl_itu_paths *incoming=calloc(1,sizeof(*incoming)+2*sizeof(incoming->profile[0]));assert(incoming);
 incoming->count=1;incoming->profile_count=2;incoming->generation=88;incoming->path[0]=paths.path[0];
 incoming->path[0].multicast=1;incoming->path[0].vlan=900;incoming->path[0].vlan_profile[0]=0;incoming->path[0].vlan_profile[4]=1;
 incoming->profile[0].downstream=8;
 incoming->profile[1]=(struct rtl_evto_profile){.input_tpid=0x8100,.output_tpid=0x8100,.count=1};
 unsigned evto[4]={0xf8000000,0x80004000|(900<<15),0x400f0000,0x80004|(901<<3)};
 for(unsigned i=0;i<4;i++)for(unsigned b=0;b<4;b++)incoming->profile[1].row[0][i*4+b]=evto[i]>>(24-b*8);
 struct net_device iptv={.ifindex=11,.vid=900,.lower=&pon};devices[11]=&iptv;fe.itu_paths=incoming;
 struct rtl9617c_bridge_frame wire_image=skb.saved;
 wire_image.pon_generation=88;wire_image.pon_gem=55;wire_image.tags=wire_image.pon_tags=1;
 wire_image.tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=901|5<<13};
 wire_image.pon_tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=900|5<<13};wire_image.pon_priority=5;
 assert(!fe_mcast_pon_input(&fe,&wire_image));
 wire_image.pon_tag[0].tci=901|5<<13;assert(fe_mcast_pon_input(&fe,&wire_image)==-EOPNOTSUPP);
 wire_image.pon_tag[0].tci=900|5<<13;
 devices[11]=NULL;assert(fe_mcast_pon_input(&fe,&wire_image)==-EOPNOTSUPP);
 free(incoming);fe.itu_paths=&paths;
 /* Wireless input uses POL/VIF identity, with metadata cleared before
  * replication so its host copy cannot become radio direct TX. */
 struct net_device wifi={.ifindex=12,.mtu=1500,.running=true,.carrier=true,.master=&br,.net=&net};
 struct rtl_fe_port wp={.fe=&fe,.netdev=&wifi,.source=18,.destination=16,.wifi_id=2,.llid=2};
 struct nf_hook_entries wh={.num_hook_entries=2,.hooks={{rtl_wifi_ingress},{fe_bridge_wire}}};
 wifi.nf_hooks_ingress=&wh;devices[12]=&wifi;fe.ports[4]=&wp;fdb_source=&wifi;
 struct rtl9617c_bridge_frame wireless=skb.frame;
 wireless.ingress=wireless.port=12;wireless.wifi_id=2;wireless.pon_gem=0;
 paths.count=1;paths.path[0].vlan=RTL_ITU_VLAN_UNTAGGED;
 for(unsigned local=0;local<2;local++) {
  fe.event_count=0;skb.dev=&wifi;skb.saved=wireless;skb.saved.multicast_epoch=fe.mc_gate.epoch;
  ticket=fe_mcast_capture_begin(&bridge,&skb,local,++fe_mcast_ticket);assert(ticket);
  fe_mcast_capture_select(&bridge,&skb,&out);fe_mcast_capture_select(&bridge,&skb,&shared);
  fe_mcast_capture_select(&bridge,&skb,&pon);
  skb.dev=&out;fe_mcast_capture_output(&bridge,&skb);skb.dev=&shared;fe_mcast_capture_output(&bridge,&skb);
  skb.dev=&pon;fe_mcast_capture_output(&bridge,&skb);
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed&&fe.indexed->input==&wp&&fe.indexed->ingress_pol==2&&fe.mc_gate.active);
  assert(fe.indexed->replica_count==3+local&&fe.indexed->replica_ports[2]==&pp);
  u32 *action=fe.memory+RTL_FE_ACTION_OFFSET+fe.indexed->hash_index*RTL_FE_ACTION_BYTES;
  assert(action[0]&(1U<<19));
  assert(!(action[0]&(255U<<20)));
  if(local) {
   struct sk_buff copy={.frame=skb.frame};
   assert(!fe_mcast_host_receive(&bridge,&copy,fe.indexed->mc_group,18)&&copy.dev==&wifi&&copy.saved.wifi_id==2);
   assert(fe_mcast_host_local(&bridge,&copy,&br,0)==1);
  }
  if(local) {
   struct sk_buff delayed={.frame=skb.frame};unsigned group=fe.indexed->mc_group;
   assert(!fe_mcast_host_receive(&bridge,&delayed,group,18));
   fe.age_fail=1;assert(rtl_fe_flush_port(&fe,&wifi)==-ETIMEDOUT&&!fe.mc_gate.active);
   assert(fe_mcast_host_receive(&bridge,&delayed,group,18)==-ESTALE);
   assert(fe_mcast_host_local(&bridge,&delayed,&br,0)==-ESTALE);
   assert(!rtl_fe_flush_port(&fe,&wifi));
  } else {fe_mcast_invalidate(&fe);assert(!fe_delete(&fe,fe.indexed));}
  struct fe_mcast_packet bad={.net=&net,.bridge=10,.epoch=fe.mc_gate.epoch,.input=wireless};
  bad.input.multicast_epoch=fe.mc_gate.epoch;bad.input.wifi_id=3;
  assert(fe_mcast_learn(&bridge,&bad)==-ESTALE);
 }
 /* A complete LAN/PON group can contain two different wireless outputs
  * plus local RX. Their immutable cookies may never alias each other,
  * the host group or a VIF reused after withdrawal. */
 struct wireless_dev ap={.iftype=NL80211_IFTYPE_AP};wifi.ieee80211_ptr=&ap;
 struct net_device wifi2=wifi;wifi2.ifindex=13;devices[13]=&wifi2;
 struct rtl_fe_port wp2=wp;wp2.netdev=&wifi2;wp2.wifi_id=wp2.llid=60;fe.ports[5]=&wp2;
 unsigned retired_cookie=0;struct fe_mcast_packet failed_packet={};
 for(unsigned scenario=0;scenario<4;scenario++) {
  struct net_device *origin=scenario==3?&onu:scenario==2?&pon:&lan;
  unsigned source=scenario==3?6:scenario==2?7:0;
  fe.event_count=0;fdb_source=origin;skb.dev=origin;skb.saved=wireless;
  skb.saved.ingress=skb.saved.port=origin->ifindex;skb.saved.wifi_id=0;
  if(scenario==3){skb.saved.ingress=6;skb.saved.tags=1;skb.saved.tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=100};}
  skb.saved.pon_gem=scenario==2?55:0;skb.saved.pon_generation=paths.generation;
  skb.saved.multicast_epoch=fe.mc_gate.epoch;skb.frame=wireless;
  ticket=fe_mcast_capture_begin(&bridge,&skb,scenario!=1,++fe_mcast_ticket);assert(ticket);
  fe_mcast_capture_select(&bridge,&skb,&wifi);fe_mcast_capture_select(&bridge,&skb,&wifi2);
  fe_mcast_capture_select(&bridge,&skb,&out);
  skb.dev=&wifi;skb.frame.tags=scenario%3;skb.frame.tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=100|5<<13};
  if(scenario==2)skb.frame.tag[1]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=200|3<<13};
  if(!skb.frame.tags)memset(skb.frame.tag,0,sizeof(skb.frame.tag));
  struct rtl9617c_bridge_frame wf=skb.frame;fe_mcast_capture_output(&bridge,&skb);
  skb.dev=&wifi2;fe_mcast_capture_output(&bridge,&skb);
  skb.dev=&out;skb.frame=wireless;fe_mcast_capture_output(&bridge,&skb);
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed&&fe.indexed->mc_host&&fe.mc_gate.active);
  struct rtl_fe_flow *learned=fe.indexed;unsigned cookie[2]={};
  for(unsigned c=1;c<256;c++)if(fe.mc_directs[c]&&fe.mc_directs[c]->flow==learned) {
   unsigned which=fe.mc_directs[c]->wifi_id==2?0:1;cookie[which]=c;
   assert(c!=learned->mc_group&&c!=retired_cookie&&fe.multicast.groups[c].state==RTL_FE_MC_RETIRED);
  }
  assert(cookie[0]&&cookie[1]&&cookie[0]!=cookie[1]);
  struct fe_mcast_packet again={.net=&net,.bridge=10,.input=skb.saved,.epoch=fe.mc_gate.epoch,
   .transaction=ticket+100,.local=scenario!=1,.count=3,.outputs=3};
  again.output[0].port=again.output[0].physical=wifi.ifindex;again.output[0].frame=wf;
  again.output[1].port=again.output[1].physical=wifi2.ifindex;again.output[1].frame=wf;
  again.output[2].port=again.output[2].physical=out.ifindex;again.output[2].frame=wireless;
  for(unsigned i=0;i<3;i++){again.output[i].logical_tags=again.output[i].frame.tags;memcpy(again.output[i].logical_tag,again.output[i].frame.tag,sizeof(again.output[i].logical_tag));}
  failed_packet=again;
  unsigned installs=fe.installs;assert(!fe_mcast_learn(&bridge,&again)&&fe.installs==installs&&fe.indexed==learned);
  ap.iftype=6;assert(fe_mcast_learn(&bridge,&again)==-EOPNOTSUPP&&fe.installs==installs);ap.iftype=NL80211_IFTYPE_AP;
  for(unsigned w=0;w<2;w++) {
   struct sk_buff copy={.frame=wf};
   assert(fe_mcast_host_receive(&bridge,&copy,cookie[w],source)==1);
   assert(copy.dev==(w?&wifi2:&wifi)&&copy.saved.wifi_id==(w?60:2));
   assert(fe_mcast_host_local(&bridge,&copy,&br,0)==-ESTALE);
   copy.frame.tag[0].tci++;assert(fe_mcast_host_receive(&bridge,&copy,cookie[w],source)==-ESTALE);
   copy.frame=wf;assert(fe_mcast_host_receive(&bridge,&copy,cookie[w],source==7?0:7)==-ESTALE);
  }
  struct sk_buff copy={.frame=again.input};
  assert(fe_mcast_host_receive(&bridge,&copy,learned->mc_group,source)==(scenario==1?-ESTALE:0));
  wifi.running=false;copy.frame=wf;assert(fe_mcast_host_receive(&bridge,&copy,cookie[0],source)==-ESTALE);wifi.running=true;
  if(scenario==3){ext_change=&fe;assert(fe_mcast_host_receive(&bridge,&copy,cookie[0],source)==-ESTALE&&!ext_change);}
  fe.age_fail=1;assert(rtl_fe_flush_port(&fe,&wifi)==-ETIMEDOUT&&!fe.mc_gate.active);
  assert(fe_mcast_host_receive(&bridge,&copy,cookie[1],source)==-ESTALE);
  assert(!rtl_fe_flush_port(&fe,&wifi));
  assert(!fe.mc_directs[cookie[0]]->flow&&!fe.mc_directs[cookie[1]]->flow);
  retired_cookie=cookie[0];
 }
 failed_packet.epoch=failed_packet.input.multicast_epoch=fe.mc_gate.epoch;
 fe.age_fail=1;
 assert(fe_mcast_learn(&bridge,&failed_packet)==-ETIMEDOUT&&!fe.mc_gate.active&&!net.refs);
 for(unsigned c=1;c<256;c++)if(fe.mc_directs[c])assert(!fe.mc_directs[c]->flow);
 /* STA destinations preserve actual software priority and dynamic COS.
  * Native ingress VLAN maps may use magic 256..263 Wi-Fi priorities. */
 ap.iftype=NL80211_IFTYPE_STATION;
 struct net_device wifi_vlan=wifi;wifi_vlan.ifindex=14;wifi_vlan.lower=&wifi;wifi_vlan.vid=200;
 wifi_vlan.ieee80211_ptr=NULL;wifi_vlan.vlan.flags=VLAN_FLAG_REORDER_HDR;devices[14]=&wifi_vlan;
 for(unsigned scenario=0;scenario<5;scenario++) {
  struct net_device *origin=scenario==0?&lan:scenario==1?&onu:scenario==2?&pon:&wifi2;
  struct net_device *dest=scenario==4?&wifi_vlan:&wifi;
  unsigned source=scenario==0?0:scenario==1?6:scenario==2?7:18;
  unsigned priority=scenario==1?262:scenario==2?5:scenario>=3?6:0;
  fe.event_count=0;fdb_source=origin;skb.dev=origin;skb.saved=wireless;skb.priority=priority;
  skb.saved.ingress=skb.saved.port=origin->ifindex;skb.saved.wifi_id=source==18?60:0;
  skb.saved.pon_gem=source==7?55:0;skb.saved.pon_generation=paths.generation;
  skb.saved.pon_priority=source==7?5:0;
  if(source==7) {
   skb.saved.tags=skb.saved.pon_tags=1;
   skb.saved.tag[0]=skb.saved.pon_tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=900|5<<13};
   paths.path[0].vlan=RTL_ITU_VLAN_ANY;
  }
  if(source==6){skb.saved.ingress=6;skb.saved.tags=1;skb.saved.tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=100|3<<13};onu.vlan.ingress_priority_map[3]=262;}
  skb.saved.multicast_epoch=fe.mc_gate.epoch;skb.frame=wireless;
  ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket);
  fe_mcast_capture_select(&bridge,&skb,dest);skb.dev=dest;fe_mcast_capture_output(&bridge,&skb);
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed&&fe.indexed->replica_count==1&&fe.mc_gate.active);
  unsigned cookie=0;
  for(unsigned c=1;c<256;c++)if(fe.mc_directs[c]&&fe.mc_directs[c]->flow==fe.indexed)cookie=c;
  assert(cookie);
  struct sk_buff copy={.frame=fe.mc_directs[cookie]->frame,.priority=2};
  assert(fe_mcast_host_receive(&bridge,&copy,cookie,source)==1);
  assert(copy.priority==(source==18?2:priority));
  ap.iftype=NL80211_IFTYPE_AP;assert(fe_mcast_host_receive(&bridge,&copy,cookie,source)==-ESTALE);ap.iftype=NL80211_IFTYPE_STATION;
  wifi.features=NETIF_F_HW_VLAN_CTAG_TX;assert(fe_mcast_host_receive(&bridge,&copy,cookie,source)==-ESTALE);wifi.features=0;
  assert(!rtl_fe_flush_port(&fe,&wifi));
  if(scenario==4) {
   /* A COS-dependent output VLAN cannot be learned from COS6 alone. */
   wifi_vlan.vlan.egress_priority_map[2]=3<<13;
   skb.dev=origin;skb.saved.multicast_epoch=fe.mc_gate.epoch;
   ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket);
   fe_mcast_capture_select(&bridge,&skb,dest);skb.dev=dest;fe_mcast_capture_output(&bridge,&skb);
   fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
   assert(!fe.indexed&&!fe.mc_gate.active);wifi_vlan.vlan.egress_priority_map[2]=0;
  }
 }
 /* Actual complete M2U transaction now publishes an immutable PE recipient
  * before exposing the Hash. Closed/foreign policies keep the CPU path. */
 struct rtl_mc_pe_table pe_table={};fe.mc_pe[1].table=&pe_table;fe.mc_pe[1].netdev=&wifi;
 test_peer_port=&wp;test_peer=(struct rtl_fe_wifi_peer){.destination=19,.macid=1,.policy_generation=1,
  .address={2,9,8,7,6,5},.policy={.qos=1,.key=7U<<16,.flags=BIT(16)|BIT(18)|BIT(23)}};
 for(unsigned v=0;v<64;v++)test_peer.policy.dscp_tid[v]=v%8;
 for(unsigned v=0;v<8;v++)test_peer.policy.priority_tid[v]=7-v;
 struct rtl_fe_mc_direct recipe={.frame=wireless,.priority=262};
 ether_addr_copy(recipe.frame.destination,test_peer.address);
 assert(!fe_mcast_pe_recipe(&fe,&wp,&recipe,6,fe.mc_gate.epoch));
 assert(((recipe.pe_record.control>>8)&7)==1&&!(recipe.pe_record.control&(3U<<11)));
 recipe.priority=0;recipe.frame.tags=1;test_peer.policy.tagged_tid=3;
 assert(!fe_mcast_pe_recipe(&fe,&wp,&recipe,7,fe.mc_gate.epoch)&&((recipe.pe_record.control>>8)&7)==3);
 recipe.frame.tags=0;recipe.priority_from_cos=true;
 assert(!fe_mcast_pe_recipe(&fe,&wp,&recipe,18,fe.mc_gate.epoch)&&((recipe.pe_record.control>>11)&3)==2);
 test_policy_generation=2;assert(fe_mcast_pe_recipe(&fe,&wp,&recipe,0,fe.mc_gate.epoch)==-EOPNOTSUPP);test_policy_generation=1;
 assert(fe_mcast_pe_recipe(&fe,&wp,&recipe,0,0x100000000ULL)==-EOPNOTSUPP);
 for(unsigned v=0;v<4;v++)fe.map[19+64*v]=9;
 fe.reg[0x6108/4]=BIT(3);fe.reg[0x6104/4]=BIT(31);
 fe.reg[(0x6148+3*4)/4]=7;fe.reg[(0x6128+7*4)/4]=6|15U<<4;
 fe.reg[(0x61e8+6*20)/4]=1;fe.reg[(0x61ec+6*20)/4]=2496U<<16|1536;
 fe.reg[(0x61f0+6*20)/4]=13;fe.reg[(0x61c8+3*4)/4]=RTL_QM_PACKET_LAYOUT;
 for(unsigned v=0;v<8;v++)fe.reg[(0x65a4+3*32+v*4)/4]=7;
 unsigned previous_cookie=0;
 for(unsigned repeat=0;repeat<3;repeat++) {
  fe.event_count=0;fdb_source=&lan;skb.dev=&lan;skb.saved=wireless;skb.priority=0;
  skb.saved.ingress=skb.saved.port=1;skb.saved.wifi_id=0;skb.saved.multicast_epoch=fe.mc_gate.epoch;
  skb.frame=wireless;
  ticket=fe_mcast_capture_begin(&bridge,&skb,true,++fe_mcast_ticket);assert(ticket);
  fe_mcast_capture_select_addr(&bridge,&skb,&wifi,test_peer.address);
  fe_mcast_capture_select(&bridge,&skb,&out);
  skb.dev=&wifi;ether_addr_copy(skb.frame.destination,test_peer.address);fe_mcast_capture_output(&bridge,&skb);
  skb.dev=&out;skb.frame=wireless;fe_mcast_capture_output(&bridge,&skb);
  fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed&&fe.mc_gate.active&&pe_table.epoch==fe.mc_gate.epoch&&fe.indexed->replica_count==3);
  unsigned cookie=0;
  for(unsigned c=1;c<256;c++)if(fe.mc_directs[c]&&fe.mc_directs[c]->flow==fe.indexed)cookie=c;
  assert(cookie&&fe.mc_directs[cookie]->pe_destination==19&&pe_table.record[cookie].epoch==pe_table.epoch);
  assert(pe_table.record[cookie].key==7U<<16&&pe_table.record[cookie].dscp_tid[46]==6);
  assert(!memcmp(pe_table.record[cookie].peer,test_peer.address,6));
  if(repeat==0 && getenv("RTL_TEST_PE_RECORD")) {
   FILE *record=fopen(getenv("RTL_TEST_PE_RECORD"),"wb");assert(record);
   assert(fwrite(&pe_table.record[cookie],sizeof(pe_table.record[cookie]),1,record)==1);assert(!fclose(record));
  }
  struct sk_buff copy={.frame=wireless};assert(fe_mcast_host_receive(&bridge,&copy,cookie,0)==-ESTALE);
  if(repeat==1)assert(cookie==previous_cookie);
  if(repeat==2)assert(cookie!=previous_cookie&&!pe_table.record[previous_cookie].epoch);
  previous_cookie=cookie;
  if(repeat!=0){
   struct rtl_fe_flow *installed=fe.indexed;
   fe_mcast_invalidate(&fe);assert(!pe_table.epoch);
   if(repeat==2){fe.age_fail=1;assert(fe_delete(&fe,installed)==-ETIMEDOUT&&!pe_table.record[cookie].epoch&&installed->deleting);}
   assert(!fe_delete(&fe,installed));assert(!pe_table.record[cookie].epoch);
   struct rtl_fe_mc_direct old=*fe.mc_directs[cookie];old.pe_record.epoch=fe.mc_gate.epoch;
   assert(fe_mcast_pe_stage(&fe,&old,cookie)==-ESTALE);
  }
 }
 /* Same STA VIF can carry multiple converted Ethernet recipients; every
  * one keeps a different cookie/DA but pins the same associated AP key. */
 fe.event_count=0;fdb_source=&lan;skb.dev=&lan;skb.saved=wireless;skb.priority=0;
 skb.saved.ingress=skb.saved.port=1;skb.saved.wifi_id=0;skb.saved.multicast_epoch=fe.mc_gate.epoch;skb.frame=wireless;
 const u8 recipients[2][6]={{2,9,8,7,6,5},{2,9,8,7,6,6}};
 ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ticket);
 for(unsigned r=0;r<2;r++)fe_mcast_capture_select_addr(&bridge,&skb,&wifi,recipients[r]);
 skb.dev=&wifi;
 for(unsigned r=0;r<2;r++){ether_addr_copy(skb.frame.destination,recipients[r]);fe_mcast_capture_output(&bridge,&skb);}
 fe_mcast_capture_end(&bridge,ticket,true);fe_mcast_process(&bridge);
 assert(fe.indexed&&fe.indexed->replica_count==2&&pe_table.epoch==fe.mc_gate.epoch);
 unsigned copied=0;
 for(unsigned c=1;c<256;c++)if(fe.mc_directs[c]&&fe.mc_directs[c]->flow==fe.indexed){
  assert(copied<2&&fe.mc_directs[c]->pe_destination==19);
  assert(!memcmp(pe_table.record[c].destination,recipients[copied],6));
  assert(!memcmp(pe_table.record[c].peer,test_peer.address,6));copied++;
 }
 assert(copied==2);fe_mcast_invalidate(&fe);assert(!fe_delete(&fe,fe.indexed));
 fe.mc_pe[1].table=NULL;fe.mc_pe[1].netdev=NULL;test_peer_port=NULL;
 /* EPON MAC-instance0 and31 use POL0/31 on ingress, with per-replica
  * POL0 and queues masked to the current PUC width on egress. */
 fe.itu_paths=NULL;fe.itu_netdev=NULL;paths.path[0].vlan=RTL_ITU_VLAN_ANY;
 struct net_device ep0=pon,ep31=pon;ep0.ifindex=9;ep31.ifindex=15;devices[9]=&ep0;devices[15]=&ep31;
 pp.netdev=&ep0;pp.llid=0;pp.destination=32;
 struct rtl_fe_port epport31={.fe=&fe,.netdev=&ep31,.source=7,.llid=31,.destination=63};fe.ports[5]=&epport31;
 for(unsigned p=32;p<64;p++)for(unsigned m=0;m<4;m++)fe.map[p+64*m]=8;
 struct rtl_epon_paths epaths={.generation=101,.count=32,.queues=4,.mode=2,.active=1U|1U<<31};
 memset(epaths.queue_mask,15,sizeof(epaths.queue_mask));
 assert(!rtl_fe_set_epon_paths(&fe,&epaths));
 for(unsigned scenario=0;scenario<4;scenario++) {
  fe.event_count=0;skb.priority=0;skb.saved=wireless;skb.saved.wifi_id=0;skb.frame=wireless;
  bool incoming=scenario<2;unsigned llid=scenario?31:0;
  struct net_device *origin=incoming?(llid?&ep31:&ep0):&lan;fdb_source=origin;skb.dev=origin;
  skb.saved.port=skb.saved.ingress=origin->ifindex;
  skb.saved.epon=incoming;skb.saved.epon_llid=incoming?llid:0;skb.saved.pon_gem=0;
  skb.saved.pon_generation=epaths.generation;skb.saved.multicast_epoch=fe.mc_gate.epoch;
  u64 ep_ticket=fe_mcast_capture_begin(&bridge,&skb,incoming,++fe_mcast_ticket);assert(ep_ticket);
  if(incoming){fe_mcast_capture_select(&bridge,&skb,&out);skb.dev=&out;fe_mcast_capture_output(&bridge,&skb);}
  else {
   fe_mcast_capture_select(&bridge,&skb,&ep0);fe_mcast_capture_select(&bridge,&skb,&ep31);
   skb.dev=&ep0;fe_mcast_capture_output(&bridge,&skb);skb.dev=&ep31;fe_mcast_capture_output(&bridge,&skb);
  }
  fe_mcast_capture_end(&bridge,ep_ticket,true);fe_mcast_process(&bridge);
  assert(fe.indexed&&fe.mc_gate.active);
  if(incoming) {
   assert(fe.indexed->ingress_pol==llid);
   struct sk_buff copy={.frame=wireless};unsigned group=fe.indexed->mc_group;
   assert(!fe_mcast_host_receive(&bridge,&copy,group,7)&&copy.dev==origin);
   assert(copy.saved.epon&&copy.saved.epon_llid==llid&&copy.saved.pon_generation==epaths.generation);
   assert(fe_mcast_host_local(&bridge,&copy,&br,0)==1);
   struct rtl_epon_paths newer=epaths;newer.generation++;newer.table[0]=123;
   fe.age_fail=1;assert(rtl_fe_set_epon_paths(&fe,&newer)==-ETIMEDOUT&&!fe.epon_admitted&&!fe.mc_gate.active);
   assert(fe.epon_paths.generation==epaths.generation);
   assert(fe_mcast_host_receive(&bridge,&copy,group,7)==-ESTALE);
   assert(!rtl_fe_set_epon_paths(&fe,&newer)&&fe.epon_admitted);epaths=newer;
  } else {
   unsigned seen=0;
   for(unsigned a=0;a<64;a++)if(fe.multicast.groups[fe.indexed->mc_group].bitmap[a/32]&BIT(a%32)) {
    assert(!(fe.actions[a][0]>>23));unsigned dest=fe.actions[a][1]>>24&63;
    assert(dest==32||dest==63);assert(!(fe.actions[a][1]&BIT(30)));seen++;
   }
   assert(seen==2);
   assert(!rtl_fe_set_epon_paths(&fe,NULL)&&!fe.epon_admitted&&!fe.epon_paths.generation);
   epaths.generation++;assert(!rtl_fe_set_epon_paths(&fe,&epaths));
  }
 }
 /* Mixed Linux recipients must all be published or all stay in software.
  * Fixed VLAN priority262 becomes queue2/6; raw Wi-Fi retains all COS only
  * with eight enabled queues and invariant final VLAN bytes. */
 fe.ports[5]=&wp2;
 for(unsigned scenario=0;scenario<6;scenario++) {
  bool dynamic=scenario>=2,disabled=scenario==1||scenario==4;
  epaths.count=scenario==0||scenario==1||scenario==3?32:16;
  epaths.queues=epaths.count>16?4:8;epaths.active=1;epaths.generation++;
  memset(epaths.queue_mask,(1U<<epaths.queues)-1,sizeof(epaths.queue_mask));
  if(disabled)epaths.queue_mask[0]&=~BIT(dynamic?2:262&(epaths.queues-1));
  assert(!rtl_fe_set_epon_paths(&fe,&epaths));
  struct net_device *origin=dynamic?&wifi2:&onu;
  fdb_source=origin;fe.event_count=0;skb.dev=origin;skb.saved=wireless;skb.frame=wireless;
  skb.priority=dynamic?6:262;skb.saved.port=origin->ifindex;
  skb.saved.ingress=dynamic?origin->ifindex:6;skb.saved.wifi_id=dynamic?60:0;
  if(!dynamic){skb.saved.tags=1;skb.saved.tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=100|3<<13};onu.vlan.ingress_priority_map[3]=262;}
  skb.saved.multicast_epoch=fe.mc_gate.epoch;
  /* Complete VLAN-derived fixed priority is also valid in eight-queue mode. */
  if(scenario==5) {
   origin=&onu;fdb_source=origin;skb.dev=origin;skb.priority=262;
   skb.saved.port=origin->ifindex;skb.saved.ingress=6;skb.saved.wifi_id=0;
   skb.saved.tags=1;skb.saved.tag[0]=(struct rtl9617c_bridge_tag){.protocol=0x8100,.tci=100|3<<13};
  }
  u64 ep_ticket=fe_mcast_capture_begin(&bridge,&skb,false,++fe_mcast_ticket);assert(ep_ticket);
  struct net_device *destinations[]={&ep0,&out,&shared,&wifi};
  for(unsigned i=0;i<4;i++)fe_mcast_capture_select(&bridge,&skb,destinations[i]);
  for(unsigned i=0;i<4;i++){skb.dev=destinations[i];fe_mcast_capture_output(&bridge,&skb);}
  fe_mcast_capture_end(&bridge,ep_ticket,true);fe_mcast_process(&bridge);
  if(disabled||scenario==3)assert(!fe.indexed&&!fe.mc_gate.active);
  else {
   assert(fe.indexed&&fe.indexed->replica_count==4&&fe.mc_gate.active);
   unsigned seen=0;
   for(unsigned a=0;a<64;a++)if(fe.multicast.groups[fe.indexed->mc_group].bitmap[a/32]&BIT(a%32)) {
    unsigned dest=fe.actions[a][1]>>24&63;
    if(dest==32){
     bool preserve=dynamic&&scenario!=5;
     assert(!(fe.actions[a][0]>>23));assert(!!(fe.actions[a][1]&BIT(30))==preserve);
     assert((fe.actions[a][2]>>5&7)==(preserve?0:262&(epaths.queues-1)));seen|=1;
    }else if(dest==1)seen|=2;else if(dest==6)seen|=4;else if(dest==16)seen|=8;
   }
   assert(seen==15);
  }
  assert(!rtl_fe_set_epon_paths(&fe,NULL)&&!fe.indexed&&!fe.mc_gate.active);
 }
 assert(!rtl_fe_set_epon_paths(&fe,NULL));
 /* Cookie pressure must fail the whole transaction before Hash publication.
  * A partially reserved recipient retains no live flow after rollback. */
 struct rtl_fe_mc_rule exhausted={.count=2};
 exhausted.direct[0].wifi_id=2;exhausted.direct[1].wifi_id=60;
 exhausted.direct[0].physical=12;exhausted.direct[1].physical=13;
 struct rtl_fe_flow incomplete={.replica_count=2};unsigned free_slot=0;
 for(unsigned c=1;c<256;c++)if(fe.multicast.groups[c].state==RTL_FE_MC_FREE) {
  if(!free_slot)free_slot=c;else fe.multicast.groups[c].state=RTL_FE_MC_RETIRED;
 }
 assert(free_slot&&fe_mcast_direct_stage(&fe,&incomplete,&exhausted)==-ENOSPC);
 assert(fe.mc_directs[free_slot]->flow==&incomplete);
 fe_mcast_host_remove(&fe,&incomplete);assert(!fe.mc_directs[free_slot]->flow);
 for(unsigned i=1;i<256;i++)free(fe.mc_directs[i]);
 for(unsigned i=0;i<16;i++)assert(!fe.mtus[i].users);
 fe_mcast_purge(&bridge);assert(!net.refs);free(fe.memory);
 puts("Native bridge multicast: complete outputs, missing/duplicate rejection, CPU host copy and host-only groups, epoch changes, shared P6, duplicate learning and generation retirement passed");
}
'''
with tempfile.TemporaryDirectory(prefix='bridge-mcast-learn-') as name:
    p=Path(name);(p/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-function','-g','-fsanitize=address,undefined',
        '-I'+str(DRIVERS),'-I'+str(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
