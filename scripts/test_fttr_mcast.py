#!/usr/bin/env python3
"""Exercise native multicast packet and gate callbacks with kernel VLAN helpers.

SKB storage, netdev topology, queue and NAPI calls are modeled. No hardware IO.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'


def function(text, name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', type=Path, required=True)
args = parser.parse_args()
kernel = (args.kernel/'net/core/skbuff.c').read_text()
source = (DRIVERS/'rtl9617c-fttr-mcast.c').read_text()
code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <arpa/inet.h>
typedef uint8_t u8;typedef uint16_t u16,__be16;typedef int netdev_tx_t;
#define ETH_HLEN 14
#define ETH_ALEN 6
#define VLAN_HLEN 4
#define VLAN_VID_MASK 4095
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define ETH_P_IP 0x0800
#define ETH_P_IPV6 0x86dd
#define IP_OFFSET 0x1fff
#define NEXTHDR_HOP 0
#define NEXTHDR_ROUTING 43
#define NEXTHDR_FRAGMENT 44
#define NEXTHDR_AUTH 51
#define NEXTHDR_DEST 60
#define NETDEV_TX_OK 0
#define BR_STATE_FORWARDING 3
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define likely(x) (x)
#define unlikely(x) (x)
#define WARN_ONCE(v,...) (v)
#define ASSERT_RTNL() assert(rtnl)
struct net_device {unsigned rtl9617c_fttr_egress;void *private;bool running,carrier,bridge,vlan,stopped;unsigned group,ifindex,mtu,stp;
 u8 dev_addr[6];struct net_device *master,*parent;unsigned vid;__be16 proto;
 struct {unsigned tx_packets,tx_bytes,tx_dropped,rx_packets,rx_bytes,rx_dropped;}stats;};
struct napi_struct {int unused;};
struct sk_buff {u8 storage[4096],*data;unsigned len,skb_iif;struct net_device *dev;
 bool tagged,rtl9617c_fttr_ingress;__be16 protocol,vlan_proto;u16 vlan_tci;unsigned mac,network_header;};
struct ethhdr {u8 addresses[12];__be16 h_proto;}__attribute__((packed));
static struct net_device parent,mcdev,service,other,bridge,wrong_bridge;
static struct napi_struct napi;
struct rtl9617c_bridge_mcast_ops {int unused;};
static const struct rtl9617c_bridge_mcast_ops ops;
static unsigned writers,gate_changes;
static const struct rtl9617c_bridge_mcast_ops *rtl9617c_bridge_mcast_begin(struct net_device *d){(void)d;assert(!writers);writers++;gate_changes++;return &ops;}
static void rtl9617c_bridge_mcast_end(const struct rtl9617c_bridge_mcast_ops *o){assert(o==&ops&&writers==1);writers--;}
static bool rtnl,cow_fail,pop_fail;static unsigned freed,delivered,submitted,purged;
static u8 received[4096],sent[4096];static unsigned received_len,sent_len;
static struct net_device *received_dev;
#define netdev_priv(d) ((d)->private)
static void rcu_read_lock(void){}
static void rcu_read_unlock(void){}
static bool netif_running(const struct net_device*d){return d->running;}
static bool netif_carrier_ok(const struct net_device*d){return d->carrier;}
static bool netif_is_bridge_master(const struct net_device*d){return d->bridge;}
static unsigned br_port_get_stp_state(const struct net_device*d){return d->stp;}
static bool is_vlan_dev(const struct net_device*d){return d->vlan;}
static struct net_device *vlan_dev_real_dev(const struct net_device*d){return d->parent;}
static struct net_device *netdev_master_upper_dev_get_rcu(const struct net_device*d){return d->master;}
static int dev_net(const struct net_device*d){(void)d;return 0;}
static struct net_device *dev_get_by_index_rcu(int net,unsigned id){(void)net;
 struct net_device *all[]={&parent,&mcdev,&service,&other};
 for(unsigned i=0;i<4;i++)if(all[i]->ifindex==id)return all[i];return NULL;}
static struct net_device *__vlan_find_dev_deep_rcu(struct net_device*d,__be16 proto,u16 vid){
 return service.parent==d && service.proto==proto && service.vid==vid?&service:NULL;}
static bool skb_vlan_tag_present(const struct sk_buff*s){return s->tagged;}
static u16 skb_vlan_tag_get_id(const struct sk_buff*s){return s->vlan_tci&4095;}
static u16 skb_vlan_tag_get(const struct sk_buff*s){return s->vlan_tci;}
static void __vlan_hwaccel_clear_tag(struct sk_buff*s){s->tagged=false;}
static void __vlan_hwaccel_put_tag(struct sk_buff*s,__be16 proto,u16 tci){s->tagged=true;s->vlan_proto=proto;s->vlan_tci=tci;}
static bool eth_type_vlan(__be16 proto){return proto==htons(0x8100)||proto==htons(0x88a8);}
static u16 get_unaligned_be16(const void*p){const u8*b=p;return b[0]*256+b[1];}
static void be16(void*p,u16 v){u8*b=p;b[0]=v>>8;b[1]=v;}
static const void *skb_header_pointer(const struct sk_buff*s,unsigned off,unsigned n,void*b){
 if(off>s->len || n>s->len-off)return NULL;memcpy(b,s->data+off,n);return b;}
static bool is_multicast_ether_addr(const u8*p){return p[0]&1;}
static bool is_broadcast_ether_addr(const u8*p){return !memcmp(p,"\xff\xff\xff\xff\xff\xff",6);}
static void ether_addr_copy(void*d,const void*s){memcpy(d,s,6);}
static int skb_cow_head(struct sk_buff*s,unsigned n){(void)s;(void)n;return cow_fail?-ENOMEM:0;}
static bool pskb_may_pull(struct sk_buff*s,unsigned n){return n<=s->len;}
static void skb_reset_mac_header(struct sk_buff*s){s->mac=s->data-s->storage;}
static u8 *skb_mac_header(struct sk_buff*s){return s->storage+s->mac;}
static struct ethhdr *eth_hdr(struct sk_buff*s){return (void *)skb_mac_header(s);}
static void skb_postpush_rcsum(struct sk_buff*s,const void*p,unsigned n){(void)s;(void)p;(void)n;}
static int __vlan_insert_tag(struct sk_buff*s,__be16 proto,u16 tci){
 assert(s->data>=s->storage+4);s->data-=4;s->len+=4;memmove(s->data,s->data+4,12);
 memcpy(s->data+12,&proto,2);be16(s->data+14,tci);s->mac-=4;return 0;}
static int __skb_vlan_pop(struct sk_buff*s,u16*tci){
 if(pop_fail||s->len<18)return -EINVAL;*tci=get_unaligned_be16(s->data+14);
 memmove(s->data+4,s->data,12);s->data+=4;s->len-=4;s->mac+=4;
 s->protocol=htons(get_unaligned_be16(s->data+12));return 0;}
static void dev_kfree_skb_any(struct sk_buff*s){freed++;free(s);}
static __be16 eth_type_trans(struct sk_buff*s,struct net_device*d){
 s->dev=d;skb_reset_mac_header(s);s->data+=14;s->len-=14;return eth_hdr(s)->h_proto;}
static unsigned flatten(struct sk_buff*s,u8*out,bool rx){
 u8 *data=rx?skb_mac_header(s):s->data;unsigned len=s->len+(rx?14:0);
 memcpy(out,data,12);unsigned n=12;if(s->tagged){memcpy(out+n,&s->vlan_proto,2);be16(out+n+2,s->vlan_tci);n+=4;}
 memcpy(out+n,data+12,len-12);return len+n-12;}
static void napi_gro_receive(struct napi_struct*n,struct sk_buff*s){assert(n==&napi);
 delivered++;received_dev=s->dev;received_len=flatten(s,received,true);dev_kfree_skb_any(s);}
static int dev_queue_xmit(struct sk_buff*s){assert(s->dev==&parent);submitted++;
 sent_len=flatten(s,sent,false);dev_kfree_skb_any(s);return 0;}
static void netif_start_queue(struct net_device*d){d->stopped=false;}
static void netif_stop_queue(struct net_device*d){d->stopped=true;}
static void netif_carrier_on(struct net_device*d){d->carrier=true;}
static void netif_carrier_off(struct net_device*d){d->carrier=false;}
static void dev_deactivate(struct net_device*d){assert(rtnl && d==&mcdev);purged++;}
static void dev_activate(struct net_device*d){assert(rtnl && d==&mcdev);}
static unsigned quiesced;
static void rtl_fttr_mcast_quiesce(struct net_device*d){assert(rtnl && d==&parent);quiesced++;}
'''
code += function(kernel, 'skb_vlan_pop') + function(kernel, 'skb_vlan_push')
code += source[source.index('struct rtl_fttr_mcast {'):source.index('static const struct net_device_ops')]
code += r'''
static struct sk_buff *packet(unsigned customer_tags,unsigned ip,bool outer,bool metadata){
 struct sk_buff*s=calloc(1,sizeof(*s));assert(s);s->data=s->storage+128;s->mac=128;s->len=14;
 memcpy(s->data,"\x01\0\x5e\0\0\x16\x02\0\0\0\0\x77",12);
 for(unsigned i=0;i<customer_tags;i++){be16(s->data+s->len-2,i?0x8100:0x88a8);be16(s->data+s->len,123+i);s->len+=4;}
 be16(s->data+s->len-2,ip==6?ETH_P_IPV6:ETH_P_IP);
 if(ip==6){s->data[s->len]=0x60;be16(s->data+s->len+4,16);s->data[s->len+6]=0;
  s->data[s->len+40]=58;s->data[s->len+48]=143;s->len+=56;
 }else{s->data[s->len]=0x45;be16(s->data+s->len+2,28);s->data[s->len+9]=2;s->data[s->len+20]=0x16;s->len+=28;}
 s->protocol=htons(get_unaligned_be16(s->data+12));s->network_header=128+14;
 if(outer){if(metadata)__vlan_hwaccel_put_tag(s,htons(0x8100),263);else{__vlan_insert_tag(s,htons(0x8100),263);s->protocol=htons(0x8100);}}
 return s;
}
int main(void){
 struct rtl_fttr_mcast mc={.parent=&parent,.admitted=true};
 parent=(struct net_device){.ifindex=1,.running=true,.carrier=true,.mtu=1982};
 bridge.bridge=bridge.running=true;mcdev=(struct net_device){.private=&mc,.ifindex=2,.running=true,.master=&bridge};
 memcpy(mcdev.dev_addr,"\x02\0\0\0\0\x88",6);
 service=(struct net_device){.ifindex=3,.running=true,.vlan=true,.parent=&parent,.master=&bridge,
  .vid=263,.proto=htons(0x8100),.group=0x46544d55,.stp=BR_STATE_FORWARDING};other.ifindex=4;
 assert(!rtl_fttr_mcast_ready(NULL) && !rtl_fttr_mcast_ready(&mcdev));
 mcdev.carrier=true;assert(rtl_fttr_mcast_ready(&mcdev));
 mc.admitted=false;assert(!rtl_fttr_mcast_ready(&mcdev));mc.admitted=true;
 parent.carrier=false;assert(!rtl_fttr_mcast_ready(&mcdev));parent.carrier=true;
 parent.running=false;assert(!rtl_fttr_mcast_ready(&mcdev));parent.running=true;
 mcdev.carrier=false;
 for(unsigned tags=0;tags<3;tags++)for(unsigned ip=4;ip<=6;ip+=2)for(unsigned metadata=0;metadata<2;metadata++){
  struct sk_buff*s=packet(tags,ip,true,metadata),*plain=packet(tags,ip,false,false);
  unsigned old=delivered;assert(rtl_fttr_mcast_receive(&mcdev,&napi,s));
  assert(delivered==old+1 && received_dev==&mcdev && received_len==plain->len && !memcmp(received,plain->data,plain->len));
  free(plain);
 }
 /* Actual service topology, malformed/truncated IP, unsupported protocol and
  * foreign GEM must not be redirected away from the original UNI. */
 for(unsigned scenario=0;scenario<17;scenario++){
  struct sk_buff*s=packet(0,scenario>=10?6:4,true,false);unsigned old=delivered;
  if(scenario==0)service.group=0x46545452; /* ordinary unicast policy */
  if(scenario==1)service.master=&wrong_bridge;
  if(scenario==2)service.running=false;
  if(scenario==3)mc.admitted=false;
  if(scenario==4)mcdev.running=false;
  if(scenario==5)be16(s->data+14,264);
  if(scenario==6)s->len=30;
  if(scenario==7)s->data[18]=0x44;
  if(scenario==8)s->data[27]=17;
  if(scenario==9)be16(s->data+24,1);
  if(scenario==10)s->data[66]=130;
  if(scenario==11)be16(s->data+22,2000);
  if(scenario==12)s->data[58]=17;
  if(scenario==13){s->data[24]=44;be16(s->data+60,8);}
  if(scenario==14){s->data[59]=9;}
  if(scenario==15)service.stp=0;
  if(scenario==16)bridge.running=false;
  assert(!rtl_fttr_mcast_receive(&mcdev,&napi,s) && delivered==old);free(s);
  service.group=0x46544d55;service.master=&bridge;service.stp=BR_STATE_FORWARDING;
  service.running=mc.admitted=mcdev.running=bridge.running=true;
 }
 for(unsigned tags=0;tags<3;tags++)for(unsigned metadata=0;metadata<2;metadata++){
  struct sk_buff*s=packet(tags,4,false,false);u8 expected[2048];unsigned len=s->len;
  memcpy(expected,s->data,12);be16(expected+12,0x8100);be16(expected+14,4095);memcpy(expected+16,s->data+12,len-12);
  if(metadata && tags){u16 tci=get_unaligned_be16(s->data+14);__be16 proto=s->protocol;__skb_vlan_pop(s,&tci);__vlan_hwaccel_put_tag(s,proto,tci);}
  unsigned old=submitted;assert(mcast_xmit(s,&mcdev)==0 && submitted==old+1);
  assert(sent_len==len+4 && !memcmp(sent,expected,sent_len));
 }
 for(unsigned scenario=0;scenario<10;scenario++){
  struct sk_buff*s=packet(0,4,false,false);unsigned old=submitted;
  if(scenario==0)s->data[0]=2;
  if(scenario==1)memset(s->data,255,6);
  if(scenario==2)memcpy(s->data,"\x01\x80\xc2\0\0\0",6);
  if(scenario==3)mc.admitted=false;
  if(scenario==4)parent.running=false;
  if(scenario==5)parent.carrier=false;
  if(scenario==6)cow_fail=true;
  if(scenario==7)s->len=10;
  if(scenario==8)s->len=1997;
  if(scenario==9){s->len=1990;s->tagged=true;}
  assert(mcast_xmit(s,&mcdev)==0 && submitted==old);
  mc.admitted=parent.running=parent.carrier=true;cow_fail=false;
 }
 for(unsigned origin=0;origin<2;origin++){
  struct sk_buff*s=packet(0,4,false,false);s->skb_iif=99;s->rtl9617c_fttr_ingress=origin;
  memcpy(s->data,"\x01\x80\xc2\0\0\x13",6);unsigned old=submitted;
  mcast_xmit(s,&mcdev);assert(submitted==old+1);
  assert(!memcmp(sent+6,origin?mcdev.dev_addr:(const u8 *)"\x02\0\0\0\0\x77",6));
 }
 /* Ordinary tagged UDP multicasts are forwarded, never mistaken for a report. */
 struct sk_buff *udp=packet(2,4,true,false);udp->data[35]=17;
 assert(!rtl_fttr_mcast_receive(&mcdev,&napi,udp));free(udp);
 pop_fail=true;struct sk_buff *bad=packet(0,4,true,false);unsigned dropped=mcdev.stats.rx_dropped;
 assert(rtl_fttr_mcast_receive(&mcdev,&napi,bad)&&mcdev.stats.rx_dropped==dropped+1);pop_fail=false;
 for(unsigned destination=0;destination<5;destination++){
  struct sk_buff*s=packet(0,4,true,false);
  const char *mac[]={"\x01\0\x5e\0\0\1","\x33\x33\0\0\0\1","\x01\x80\xc2\0\0\0","\xff\xff\xff\xff\xff\xff","\x02\0\0\0\0\1"};
  memcpy(s->data,mac[destination],6);assert(rtl_fttr_mcast_drop(&mcdev,s)==(destination<2));
  mcdev.running=false;assert(!rtl_fttr_mcast_drop(&mcdev,s));mcdev.running=true;
  be16(s->data+14,4095);assert(!rtl_fttr_mcast_drop(&mcdev,s));
  mc.admitted=false;assert(rtl_fttr_mcast_drop(&mcdev,s));mc.admitted=true;
  assert(rtl_fttr_mcast_drop(NULL,s));free(s);
 }
 rtnl=true;rtl_fttr_mcast_gate(&mcdev,false);assert(!mc.admitted&&!mcdev.carrier&&purged==1);
 rtl_fttr_mcast_gate(&mcdev,true);assert(mc.admitted&&mcdev.carrier);
 parent.carrier=false;rtl_fttr_mcast_link(&mcdev,false);assert(!mcdev.carrier);
 parent.running=false;assert(mcast_open(&mcdev)==-ENETDOWN);parent.running=true;parent.carrier=true;
 assert(!mcast_open(&mcdev)&&!mcdev.stopped);assert(!mcast_stop(&mcdev)&&mcdev.stopped&&!mcdev.carrier&&quiesced==1);
 assert(mcast_iflink(&mcdev)==1);
 assert(!writers && gate_changes>=2);
 puts("PASS: mixed VLAN/IGMP/MLD reports, GEM4095 TX, source/duplicate rules, malformed packets and forwarding gate; native callbacks with Linux VLAN helpers");
}
'''
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)
    (path/'test.c').write_text(code)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-function',
        '-Wno-sign-compare','-Wno-misleading-indentation','-fsanitize=address,undefined',
        str(path/'test.c'),'-o',str(path/'test')], check=True)
    subprocess.run([str(path/'test')], check=True)
