#!/usr/bin/env python3
"""Run bridge path capture, DIRECT lookup and paired HW teardown on an FDB move."""
from pathlib import Path
import argparse
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', type=Path, default=root / 'sources/openwrt/build_dir/target-aarch64_cortex-a55_musl/linux-rtl9617c_generic/linux-6.18.55')
parser.add_argument('--before', action='store_true')
args = parser.parse_args()


def function(source, name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    if source[start:].startswith(name + '('):
        start = source.rfind('\n', 0, start - 1) + 1
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16,__u16,__be16;typedef uint32_t u32;
#define ETH_ALEN 6
#define NF_FLOW_TABLE_ENCAP_MAX 2
#define FLOW_OFFLOAD_DIR_MAX 2
#define NF_FLOW_OFFLOAD_ROUTE 1
#define NFPROTO_IPV4 2
#define NFPROTO_IPV6 10
enum flow_offload_xmit_type {FLOW_OFFLOAD_XMIT_UNSPEC,FLOW_OFFLOAD_XMIT_NEIGH,FLOW_OFFLOAD_XMIT_XFRM,FLOW_OFFLOAD_XMIT_DIRECT};
enum flow_offload_tuple_dir {FLOW_OFFLOAD_DIR_ORIGINAL,FLOW_OFFLOAD_DIR_REPLY};
enum ip_conntrack_dir {IP_CT_DIR_ORIGINAL,IP_CT_DIR_REPLY};
enum {DEV_PATH_ETHERNET,DEV_PATH_DSA,DEV_PATH_VLAN,DEV_PATH_PPPOE,DEV_PATH_BRIDGE};
enum {DEV_PATH_BR_VLAN_UNTAG_HW,DEV_PATH_BR_VLAN_TAG,DEV_PATH_BR_VLAN_UNTAG,DEV_PATH_BR_VLAN_KEEP};
enum {NF_FLOW_TEARDOWN,NF_FLOW_HW,NF_FLOW_HW_DYING,NF_FLOW_HW_DEAD,NF_FLOW_CLOSING,NF_FLOW_HW_PENDING};
#define BIT(n) (1UL<<(n))
#define test_bit(n,p) (!!(*(p)&BIT(n)))
#define test_bit_acquire(n,p) test_bit(n,p)
static bool test_and_set_bit(unsigned n,unsigned long *p){
 bool old=test_bit(n,p);*p|=BIT(n);return old;
}
#define unlikely(v) (v)
static bool warn_once(bool value){assert(!value);return value;}
#define WARN_ON_ONCE(v) warn_once(v)
#define EXPORT_SYMBOL_GPL(n)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct net {unsigned id;};
struct net_device {unsigned ifindex;struct net *net;u8 dev_addr[6];};
struct dst_entry {struct net_device *dev;};
struct nf_conn {struct net *net;bool dying,offloaded;};
struct flow_offload_tuple {
 unsigned dir,xmit_type,l3proto,mtu,iifidx,encap_num,in_vlan_ingress,ifidx,dst_cookie;
 struct {u16 id;__be16 proto;}encap[2];struct dst_entry *dst_cache;
 struct {u32 ifidx,hw_ifidx,bridge_ifidx,bridge_port;u16 bridge_vid;u8 h_source[6],h_dest[6];}out;
};
struct flow_offload_tuple_rhash {struct flow_offload_tuple tuple;};
struct flow_offload {struct nf_conn *ct;unsigned type;unsigned long flags;struct flow_offload_tuple_rhash tuplehash[2];bool hw_rules[2];};
struct nf_flowtable {struct flow_offload_tuple_rhash *rhashtable;bool hw;};
struct nft_flowtable {struct nf_flowtable data;};
struct nf_flow_route {struct {struct dst_entry *dst;struct {u32 ifindex;struct {u16 id;__be16 proto;}encap[2];unsigned num_encaps,ingress_vlans;}in;
 struct {u32 ifindex,hw_ifindex,bridge_ifindex,bridge_port;u16 bridge_vid;u8 h_source[6],h_dest[6];}out;unsigned xmit_type;}tuple[2];};
struct net_device_path {unsigned type;struct net_device *dev;struct {unsigned vlan_mode;u16 vlan_id,vlan_proto;}bridge;struct {u16 id,proto;u8 h_dest[6];}encap;};
struct net_device_path_stack {int num_paths;struct net_device_path path[4];};
static struct net_device_path_stack path_stack;
static struct net_device bridge[2],ports[2][2];
static unsigned moved,hw_deletes,freed;
static int nf_flow_offload_rhash_params;
static struct net *nf_ct_net(struct nf_conn *ct){return ct->net;}
static struct net_device *dev_get_by_index_rcu(struct net *net,unsigned index){return bridge[net->id].ifindex==index?&bridge[net->id]:NULL;}
static struct net_device *br_fdb_find_port_rcu(struct net_device *br,const u8 *mac,u16 vid){
 return &ports[br->net->id][br->net->id==0&&mac[5]==1&&vid==7&&moved];}
static void flow_offload_teardown(struct flow_offload *f){f->flags|=BIT(NF_FLOW_TEARDOWN);f->ct->offloaded=false;}
static void *rhashtable_lookup(void *table,void *key,int params){(void)key;(void)params;return *(void **)table;}
static bool nf_ct_is_dying(struct nf_conn *ct){return ct->dying;}
static bool nf_flow_has_expired(struct flow_offload *f){(void)f;return false;}
static bool nf_flow_custom_gc(struct nf_flowtable *ft,struct flow_offload *f){(void)ft;(void)f;return false;}
static void nf_flow_table_extend_ct_timeout(struct nf_conn *ct){(void)ct;}
static void nf_flow_offload_stats(struct nf_flowtable *ft,struct flow_offload *f){(void)ft;(void)f;}
static void nf_flow_offload_del(struct nf_flowtable *ft,struct flow_offload *f){
 (void)ft;assert(test_bit(NF_FLOW_TEARDOWN,&f->flags));f->hw_rules[0]=f->hw_rules[1]=false;hw_deletes+=2;
 f->flags|=BIT(NF_FLOW_HW_DYING)|BIT(NF_FLOW_HW_DEAD);}
static void flow_offload_del(struct nf_flowtable *ft,struct flow_offload *f){(void)ft;(void)f;freed++;}
static bool is_zero_ether_addr(const u8 *a){static const u8 zero[6];return !memcmp(a,zero,6);}
static bool nf_flowtable_hw_offload(struct nf_flowtable *ft){return ft->hw;}
static bool nft_is_valid_ether_device(const struct net_device *d){return d!=NULL;}
static bool nft_flowtable_find_dev(const struct net_device *d,struct nft_flowtable *ft){(void)ft;return d!=NULL;}
static int nft_dev_fill_forward_path(struct nf_flow_route *r,const struct dst_entry *d,const struct nf_conn *ct,unsigned dir,u8 *ha,struct net_device_path_stack *s){
 (void)r;(void)d;(void)ct;(void)dir;*s=path_stack;memset(ha,0,6);ha[5]=1;return 0;}
static struct dst_entry *nft_route_dst_fetch(struct nf_flow_route *r,unsigned dir){return r->tuple[dir].dst;}
static unsigned ip_dst_mtu_maybe_forward(struct dst_entry *dst,bool f){(void)dst;(void)f;return 1500;}
static unsigned ip6_dst_mtu_maybe_forward(struct dst_entry *dst,bool f){return ip_dst_mtu_maybe_forward(dst,f);}
static void dst_release(struct dst_entry *dst){(void)dst;}
static unsigned flow_offload_dst_cookie(struct flow_offload_tuple *t){(void)t;return 0;}
'''
cases = r'''
int main(void){
 for(unsigned ipv6=0;ipv6<2;ipv6++){
 moved=hw_deletes=freed=0;
 struct net nets[2]={{0},{1}};struct nf_flowtable ft={.hw=true};
 for(unsigned n=0;n<2;n++){bridge[n]=(struct net_device){.ifindex=10,.net=&nets[n]};ports[n][0]=(struct net_device){.ifindex=2,.net=&nets[n]};ports[n][1]=(struct net_device){.ifindex=3,.net=&nets[n]};}
 struct net_device physical={.ifindex=4,.net=&nets[0]};struct dst_entry dst={&bridge[0]};
 /* VLAN logical port2 and physical port4 must not be confused. */
 path_stack=(struct net_device_path_stack){.num_paths=3,.path={
  {.type=DEV_PATH_BRIDGE,.dev=&bridge[0],.bridge={DEV_PATH_BR_VLAN_KEEP,7,0x8100}},
  {.type=DEV_PATH_VLAN,.dev=&ports[0][0],.encap={7,0x8100,{0}}},
  {.type=DEV_PATH_ETHERNET,.dev=&physical}}};
 struct nf_conn ct={.net=&nets[0],.offloaded=true};
 struct nf_flow_route route={};route.tuple[0].dst=&dst;
 struct nft_flowtable nft={.data={.hw=true}};
 nft_dev_forward_path(&route,&ct,0,&nft);
 struct flow_offload flow={.ct=&ct,.type=NF_FLOW_OFFLOAD_ROUTE,.flags=BIT(NF_FLOW_HW),.hw_rules={true,true}};
 flow.tuplehash[1].tuple.dir=1;
 flow.tuplehash[0].tuple.l3proto=ipv6?NFPROTO_IPV6:NFPROTO_IPV4;
 assert(!flow_offload_fill_route(&flow,&route,0));
 struct flow_offload_tuple *tuple=&flow.tuplehash[0].tuple;
 assert(tuple->out.bridge_ifidx==10 && tuple->out.bridge_port==2 && tuple->out.bridge_vid==7 && tuple->out.hw_ifidx==4);
 ft.rhashtable=&flow.tuplehash[0];assert(flow_offload_lookup(&ft,tuple));
 moved=1;
 nf_flow_offload_gc_step(&ft,&flow,NULL);
 assert(!ct.offloaded && test_bit(NF_FLOW_TEARDOWN,&flow.flags) && hw_deletes==2 && !flow.hw_rules[0] && !flow.hw_rules[1]);
 for(unsigned dir=0;dir<2;dir++){ft.rhashtable=&flow.tuplehash[dir];assert(!flow_offload_lookup(&ft,&flow.tuplehash[dir].tuple));}
 nf_flow_offload_gc_step(&ft,&flow,NULL);assert(freed==1);
 /* A move detected by software lookup tears down the same paired connection. */
 flow.flags=0;ct.offloaded=true;ft.rhashtable=&flow.tuplehash[0];
 assert(!flow_offload_lookup(&ft,tuple) && !ct.offloaded);
 /* Same MAC in another VLAN/namespace, unrelated MAC and nonbridge routes survive. */
 for(unsigned control=0;control<4;control++){
  flow.flags=0;ct.offloaded=true;ct.net=&nets[0];tuple->out.bridge_ifidx=10;tuple->out.bridge_vid=7;tuple->out.h_dest[5]=1;
  if(control==0)tuple->out.bridge_vid=8;
  if(control==1)ct.net=&nets[1];
  if(control==2)tuple->out.h_dest[5]=9;
  if(control==3)tuple->out.bridge_ifidx=0;
  assert(flow_offload_lookup(&ft,tuple) && ct.offloaded);
 }
 }
 puts("Bridge move: IPv4/IPv6 path/VLAN capture -> software miss -> paired hardware removal -> connection released; unrelated flows retained.");
}
'''
with tempfile.TemporaryDirectory(prefix='flowtable-roam-') as directory:
    work = Path(directory)
    kernel = args.kernel
    if args.before:
        patch = root / 'port/overlay/target/linux/rtl9617c/patches-6.18/216-flowtable-bridge-move.patch'
        for name in re.findall(r'^\+\+\+ b/(.+)$', patch.read_text(), re.M):
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(kernel / name, target)
        subprocess.run(['patch', '-R', '-p1', '--fuzz=0', '-i', str(patch)], cwd=work, check=True, stdout=subprocess.DEVNULL)
        kernel = work
    core = (kernel / 'net/netfilter/nf_flow_table_core.c').read_text()
    path = (kernel / 'net/netfilter/nf_flow_table_path.c').read_text()
    code = common + re.search(r'struct nft_forward_info \{.*?\n\};', path, re.S)[0] + '\n'
    code += function(path, 'nft_dev_path_info') + function(path, 'nft_dev_forward_path')
    code += function(core, 'flow_offload_fill_route')
    if 'static bool nf_flow_bridge_changed(' in core:
        code += function(core, 'nf_flow_bridge_changed')
    code += function(core, 'flow_offload_lookup') + function(core, 'nf_flow_offload_gc_step') + cases
    source, binary = work / 'roam.c', work / 'roam'
    source.write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-parameter', '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
