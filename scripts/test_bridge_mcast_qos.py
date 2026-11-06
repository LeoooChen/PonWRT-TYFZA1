#!/usr/bin/env python3
"""Compare native multicast VLAN/priority recipes with actual Linux TX and WME."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', type=Path, required=True)
args = parser.parse_args()


def function(text, name):
    m = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^;]*?\)\s*\{', text, re.M)
    start = m.start()
    end, depth = text.index('{', start) + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


native = (drivers / 'rtl9617c-fe-mcast-learn.h').read_text()
vlan = (args.kernel / 'net/8021q/vlan_dev.c').read_text()
vlan_header = (args.kernel / 'net/8021q/vlan.h').read_text()
core = (args.kernel / 'net/core/dev.c').read_text()
wireless = (args.kernel / 'net/wireless/util.c').read_text()
wme = (args.kernel / 'net/mac80211/wme.c').read_text()
code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <arpa/inet.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#undef htons
#define htons(v) __builtin_bswap16(v)
typedef uint8_t u8;typedef uint16_t u16,__be16;typedef uint32_t u32;typedef uint64_t u64,netdev_features_t;
#include "linux/rtl9617c-bridge.h"
#define ETH_HLEN 14
#define ETH_P_IP 0x800
#define ETH_P_IPV6 0x86dd
#define ETH_P_MPLS_UC 0x8847
#define ETH_P_MPLS_MC 0x8848
#define ETH_P_80221 0x8917
#define VLAN_HLEN 4
#define VLAN_VID_MASK 4095
#define VLAN_PRIO_MASK 0xe000
#define VLAN_PRIO_SHIFT 13
#define VLAN_FLAG_REORDER_HDR 1
#define VLAN_FLAG_GVRP 2
#define VLAN_FLAG_LOOSE_BINDING 4
#define VLAN_FLAG_MVRP 8
#define VLAN_FLAG_BRIDGE_BINDING 16
#define CONFIG_ARCH_RTL9617C 1
#define NETIF_F_HW_VLAN_CTAG_TX 1
#define NETIF_F_HW_VLAN_STAG_TX 2
#define NET_XMIT_SUCCESS 0
#define NET_XMIT_CN 2
#define NL80211_IFTYPE_MESH_POINT 7
#define NL80211_IFTYPE_OCB 11
#define IEEE80211_NUM_TIDS 16
#define IEEE80211_AC_BE 2
#define IEEE80211_AC_BK 3
#define IEEE80211_AC_VI 1
#define IEEE80211_AC_VO 0
#define BIT(n) (1U<<(n))
#define READ_ONCE(v) (v)
#define likely(v) (v)
#define unlikely(v) (v)
#define rcu_dereference(v) (v)
#define array_index_nospec(i,n) (i)
#define MPLS_LS_TC_MASK 0xe00
#define MPLS_LS_TC_SHIFT 9
struct mpls_label {u32 entry;};
struct vlan_pcpu_stats {unsigned syncp,tx_packets,tx_bytes,tx_dropped;};
struct vlan_dev_priv {struct net_device *real_dev;u16 vlan_proto,vlan_id,flags;u32 ingress_priority_map[8];u16 egress_priority_map[8];struct vlan_pcpu_stats *vlan_pcpu_stats;};
struct net_device {bool vlan;netdev_features_t features;struct vlan_dev_priv v;};
struct rtl_fe_port {struct net_device *netdev;unsigned wifi_id;};
struct rtl_fe {struct net_device *itu_netdev;};
struct sk_buff {struct net_device *dev;u8 data[128];unsigned len,priority;__be16 protocol,vlan_proto;u16 vlan_tci;bool tagged;};
struct ethhdr {u8 dest[6],source[6];__be16 protocol;} __attribute__((packed));
struct vlan_ethhdr {u8 dest[6],source[6];__be16 h_vlan_proto;u16 tci,protocol;} __attribute__((packed));
typedef int netdev_tx_t;
static struct vlan_dev_priv *vlan_dev_priv(const struct net_device *d){return (struct vlan_dev_priv *)&d->v;}
static bool is_vlan_dev(struct net_device *d){return d->vlan;}
static bool netif_running(const struct net_device *d){(void)d;return false;}
static void vlan_gvrp_request_join(const struct net_device *d){(void)d;abort();}
static void vlan_gvrp_request_leave(const struct net_device *d){(void)d;abort();}
static void vlan_mvrp_request_join(const struct net_device *d){(void)d;abort();}
static void vlan_mvrp_request_leave(const struct net_device *d){(void)d;abort();}
static unsigned changes,writing,old_flags,new_flags;
static struct net_device *changed;
static const struct rtl9617c_bridge_mcast_ops change_ops;
const struct rtl9617c_bridge_mcast_ops *rtl9617c_bridge_mcast_begin(struct net_device *d){assert(!d&&!writing&&changed->v.flags==old_flags);writing++;changes++;return &change_ops;}
void rtl9617c_bridge_mcast_end(const struct rtl9617c_bridge_mcast_ops *o){assert(o==&change_ops&&writing==1&&changed->v.flags==new_flags);writing--;}
static struct net_device *vlan_dev_real_dev(struct net_device *d){return d->v.real_dev;}
static __be16 vlan_dev_vlan_proto(struct net_device *d){return d->v.vlan_proto;}
static u16 vlan_dev_vlan_id(struct net_device *d){return d->v.vlan_id;}
static u16 vlan_dev_get_egress_qos_mask(struct net_device *d,u32 p){return d->v.egress_priority_map[p&7];}
static bool netpoll_tx_running(struct net_device *d){(void)d;return false;}
static int vlan_netpoll_send_skb(struct vlan_dev_priv *v,struct sk_buff *s){(void)v;(void)s;abort();}
#define this_cpu_ptr(v) (v)
#define this_cpu_inc(v) (++(v))
static void u64_stats_update_begin(unsigned *p){(void)p;}
static void u64_stats_update_end(unsigned *p){(void)p;}
static void u64_stats_inc(unsigned *p){++*p;}
static void u64_stats_add(unsigned *p,unsigned n){*p+=n;}
static bool skb_vlan_tag_present(struct sk_buff *s){return s->tagged;}
static u16 skb_vlan_tag_get(struct sk_buff *s){return s->vlan_tci;}
static void __vlan_hwaccel_put_tag(struct sk_buff *s,__be16 proto,u16 tci){assert(!s->tagged);s->tagged=true;s->vlan_proto=proto;s->vlan_tci=tci;}
static bool vlan_hw_offload_capable(netdev_features_t features,__be16 proto){return features & (proto==htons(0x8100)?1:2);}
static struct sk_buff *__vlan_hwaccel_push_inside(struct sk_buff *s){
 assert(s->tagged&&s->len+4<=sizeof(s->data));memmove(s->data+16,s->data+12,s->len-12);
 memcpy(s->data+12,&s->vlan_proto,2);u16 tci=htons(s->vlan_tci);memcpy(s->data+14,&tci,2);
 s->len+=4;s->protocol=s->vlan_proto;s->tagged=false;return s;
}
static int dev_queue_xmit(struct sk_buff *s);
struct cfg80211_qos_map {unsigned num_des;struct {u8 dscp,up;}dscp_exception[21];struct {u8 low,high;}up[8];};
struct mac80211_qos_map {struct cfg80211_qos_map qos_map;};
struct ieee80211_if_managed {struct {unsigned admitted_time,up;}tx_tspec[4];};
struct ieee80211_sub_if_data {struct {unsigned type;}vif;unsigned wmm_acm;__be16 control_port_protocol;struct mac80211_qos_map *qos_map;union {struct ieee80211_if_managed mgd;}u;};
struct sta_info {struct {bool wme;}sta;unsigned reserved_tid;};
static void skb_get_hash(struct sk_buff *s){(void)s;}
static bool is_multicast_ether_addr(const u8 *p){return p[0]&1;}
static void *skb_header_pointer(struct sk_buff *s,unsigned off,unsigned n,void *storage){if(off+n>s->len)return NULL;memcpy(storage,s->data+off,n);return storage;}
static u8 ipv4_get_dsfield(const struct iphdr *h){return h->tos;}
static u8 ipv6_get_dsfield(const struct ipv6hdr *h){const u8 *p=(const void *)h;return (p[0]&15)<<4|p[1]>>4;}
'''
# Match Linux's name in the exact WME function.
code = code.replace('u8 dest[6],source[6];__be16 protocol;', 'u8 h_dest[6],h_source[6];__be16 protocol;')
code += function(native, 'fe_mcast_physical') + function(native, 'fe_mcast_input_priority')
code += function(vlan_header, 'vlan_get_ingress_priority')
code += function(core, 'validate_xmit_vlan') + function(vlan, 'vlan_dev_hard_start_xmit')
code += function(vlan, 'vlan_dev_change_flags')
code += function(wireless, 'cfg80211_classify8021d')
code += wme[wme.index('const int ieee802_1d_to_ac'):wme.index('static int wme_downgrade_ac')]
for name in ('wme_downgrade_ac', 'ieee80211_fix_reserved_tid', 'ieee80211_downgrade_queue', 'ieee80211_select_queue'):
    code += function(wme, name)
code += r'''
static struct ieee80211_sub_if_data sdata;
static struct sta_info sta={.sta={true},.reserved_tid=16};
static struct sk_buff delivered;
static unsigned delivered_queue;
static int dev_queue_xmit(struct sk_buff *s){
 assert(validate_xmit_vlan(s,s->dev->features)==s);
 if(is_vlan_dev(s->dev))return vlan_dev_hard_start_xmit(s,s->dev);
 delivered_queue=ieee80211_select_queue(&sdata,&sta,s);delivered=*s;return 0;
}
static void frame_tags(struct sk_buff *s,struct rtl9617c_bridge_frame *f){
 unsigned offset=12;u16 proto;memcpy(&proto,s->data+offset,2);proto=ntohs(proto);
 while(proto==0x8100||proto==0x88a8){
  assert(f->tags<2);u16 tci;memcpy(&tci,s->data+offset+2,2);
  f->tag[f->tags++]=(struct rtl9617c_bridge_tag){.protocol=proto,.tci=ntohs(tci)};
  offset+=4;memcpy(&proto,s->data+offset,2);proto=ntohs(proto);
 }
 f->protocol=proto;
}
int main(void){
 struct vlan_pcpu_stats stats={};struct net_device radio={},input={};struct rtl_fe fe={};
 struct rtl_fe_port ingress={.netdev=&input};unsigned cases=0;
 struct net_device low={.vlan=true,.v={.real_dev=&radio,.vlan_proto=htons(0x8100),.vlan_id=100,.vlan_pcpu_stats=&stats}};
 struct net_device high={.vlan=true,.v={.real_dev=&low,.vlan_proto=htons(0x88a8),.vlan_id=200,.vlan_pcpu_stats=&stats}};
 struct net_device source={.vlan=true,.v={.real_dev=&input,.vlan_proto=htons(0x8100),.vlan_id=300}};
 struct mac80211_qos_map mapping={.qos_map={.num_des=1,.dscp_exception={{46,1}}}};
 for(unsigned ipv6=0;ipv6<2;ipv6++)for(unsigned depth=0;depth<3;depth++)
 for(unsigned reorder=0;reorder<2;reorder++)for(unsigned pcp=0;pcp<8;pcp++)
 for(unsigned dscp=0;dscp<64;dscp++)for(unsigned magic=0;magic<2;magic++) {
  struct sk_buff sw={.dev=depth==2?&high:depth?&low:&radio,.len=78,.priority=magic?256+pcp:pcp};
  sw.data[0]=1;sw.data[6]=2;sw.protocol=htons(ipv6?ETH_P_IPV6:ETH_P_IP);memcpy(sw.data+12,&sw.protocol,2);
  if(ipv6){sw.data[14]=0x60|(dscp>>4);sw.data[15]=(dscp&15)<<4;sw.data[20]=17;}
  else {sw.data[14]=0x45;sw.data[15]=dscp<<2;sw.data[23]=17;}
  struct sk_buff accelerated=sw;
  struct rtl9617c_bridge_frame native={};frame_tags(&sw,&native);
  low.v.flags=high.v.flags=reorder?VLAN_FLAG_REORDER_HDR:0;
  for(unsigned p=0;p<8;p++){low.v.egress_priority_map[p]=p<<13;high.v.egress_priority_map[p]=(7-p)<<13;}
  assert(fe_mcast_physical(sw.dev,&native,sw.priority)==&radio);
  sdata.qos_map=dscp&1?&mapping:NULL;sdata.wmm_acm=pcp&1?BIT(6)|BIT(7):0;sta.reserved_tid=pcp==7?7:16;
  dev_queue_xmit(&sw);struct sk_buff expected=delivered;unsigned queue=delivered_queue;
  for(unsigned tag=native.tags;tag;tag--){
   __vlan_hwaccel_put_tag(&accelerated,htons(native.tag[tag-1].protocol),native.tag[tag-1].tci);
   __vlan_hwaccel_push_inside(&accelerated);
  }
  accelerated.dev=&radio;dev_queue_xmit(&accelerated);
  assert(delivered.len==expected.len&&!memcmp(delivered.data,expected.data,expected.len));
  assert(delivered.protocol==expected.protocol&&delivered.priority==expected.priority&&delivered_queue==queue);
  struct rtl9617c_bridge_frame received={.tags=1,.tag={{.protocol=0x8100,.tci=300|pcp<<13}}};
  source.v.ingress_priority_map[pcp]=magic?256+pcp:pcp;u32 priority;bool dynamic;
  assert(!fe_mcast_input_priority(&fe,&ingress,&source,&received,&priority,&dynamic));
  assert(!dynamic&&priority==vlan_get_ingress_priority(&source,received.tag[0].tci));cases++;
 }
 /* Same-protocol no-reorder output must not add a duplicate tag. */
 struct rtl9617c_bridge_frame tags={.tags=1,.tag={{.protocol=0x8100,.tci=222|5<<13}}};
 low.v.flags=0;assert(fe_mcast_physical(&low,&tags,3)==&radio&&tags.tags==1&&tags.tag[0].tci==(222|5<<13));
 struct sk_buff no_reorder={.dev=&low,.len=78,.priority=3,.protocol=htons(ETH_P_IP)};
 no_reorder.data[14]=0x45;memcpy(no_reorder.data+12,&no_reorder.protocol,2);
 __vlan_hwaccel_put_tag(&no_reorder,htons(0x8100),222|5<<13);
 dev_queue_xmit(&no_reorder);struct rtl9617c_bridge_frame actual={};frame_tags(&delivered,&actual);
 assert(actual.tags==tags.tags&&!memcmp(actual.tag,tags.tag,sizeof(tags.tag)));
 low.v.flags=VLAN_FLAG_REORDER_HDR;assert(fe_mcast_physical(&low,&tags,3)==&radio&&tags.tags==2);
 assert(!fe_mcast_physical(&low,&tags,3));
 low.features=NETIF_F_HW_VLAN_CTAG_TX;tags.tags=0;assert(!fe_mcast_physical(&low,&tags,0));
 /* Magic priority must survive: COS6 would otherwise classify DSCP0 as BE. */
 struct sk_buff magic={.len=78,.priority=262,.protocol=htons(ETH_P_IP)};magic.data[14]=0x45;
 sdata.wmm_acm=0;sdata.qos_map=NULL;sta.reserved_tid=16;
 assert(ieee80211_select_queue(&sdata,&sta,&magic)==IEEE80211_AC_VO);
 magic.priority=6;assert(ieee80211_select_queue(&sdata,&sta,&magic)==IEEE80211_AC_BE);
 changed=&low;old_flags=low.v.flags;new_flags=0;
 assert(!vlan_dev_change_flags(&low,0,VLAN_FLAG_REORDER_HDR)&&changes==1&&!writing);
 old_flags=0;new_flags=VLAN_FLAG_REORDER_HDR;
 assert(!vlan_dev_change_flags(&low,new_flags,VLAN_FLAG_REORDER_HDR)&&changes==2&&!writing);
 assert(vlan_dev_change_flags(&low,0,128)==-EINVAL&&changes==2&&!writing);
 printf("Multicast STA QoS: %u actual Linux VLAN TX/WME comparisons, all DSCP/PCP, magic priority, maps/ACM/reserved TID and native ingress mapping passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='mcast-sta-qos-') as work:
    work = Path(work)
    (work / 'test.c').write_text(code)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fsanitize=address,undefined', '-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),
                    str(work/'test.c'), '-o', str(work/'test')], check=True)
    subprocess.run([str(work/'test')], check=True)
