#!/usr/bin/env python3
"""Execute Linux STA group RA/key selection and its actual ToDS/A-MSDU headers."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel',type=Path,required=True)
args=parser.parse_args()
tx=(args.kernel/'net/mac80211/tx.c').read_text()
def function(name):
    m=re.search(r'^(?:static )?[^\n]*\b'+name+r'\([^;]*?\)\s*\{',tx,re.M)
    start=m.start()
    if tx[start:].startswith(name+'('):start=tx.rfind('\n',0,start-1)+1
    end=tx.index('{',start)+1;depth=1
    while depth:
        depth+=(tx[end]=='{')-(tx[end]=='}');end+=1
    return tx[start:end]+'\n'

code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stddef.h>
typedef uint8_t u8;typedef uint16_t u16;typedef unsigned ieee80211_tx_result;
#define ETH_ALEN 6
#define BIT(n) (1U<<(n))
#define unlikely(v) (v)
#define rcu_dereference(v) (v)
#define ERR_PTR(v) ((void *)(intptr_t)(v))
#define WARN_ON_ONCE(v) (v)
#define debug_noinline
#define fallthrough __attribute__((fallthrough))
#define cpu_to_le16(v) (v)
#define cpu_to_be16(v) __builtin_bswap16(v)
#define IEEE80211_FCTL_TODS 0x100
#define IEEE80211_TX_INTFL_DONT_ENCRYPT BIT(0)
#define IEEE80211_TX_CTL_HW_80211_ENCAP BIT(1)
#define IEEE80211_TX_CTL_RATE_CTRL_PROBE BIT(2)
#define IEEE80211_TX_CTRL_AMSDU BIT(0)
#define IEEE80211_TX_CTRL_MLO_LINK 15
#define IEEE80211_LINK_UNSPECIFIED 15
#define IEEE80211_KEY_FLAG_SW_MGMT_TX BIT(0)
#define KEY_FLAG_UPLOADED_TO_HARDWARE BIT(0)
#define KEY_FLAG_TAINTED BIT(1)
#define IEEE80211_QOS_CTL_A_MSDU_PRESENT 0x80
#define WIPHY_FLAG_SUPPORTS_TDLS BIT(0)
#define WLAN_STA_TDLS_PEER 0
#define WLAN_STA_TDLS_PEER_AUTH 1
#define WLAN_STA_USES_ENCRYPTION 2
#define TX_CONTINUE 0
#define TX_DROP 1
enum {NL80211_IFTYPE_AP_VLAN,NL80211_IFTYPE_AP,NL80211_IFTYPE_OCB,NL80211_IFTYPE_ADHOC,NL80211_IFTYPE_STATION};
enum {WLAN_CIPHER_SUITE_WEP40,WLAN_CIPHER_SUITE_WEP104,WLAN_CIPHER_SUITE_TKIP,WLAN_CIPHER_SUITE_CCMP,WLAN_CIPHER_SUITE_CCMP_256,WLAN_CIPHER_SUITE_GCMP,WLAN_CIPHER_SUITE_GCMP_256,WLAN_CIPHER_SUITE_AES_CMAC,WLAN_CIPHER_SUITE_BIP_CMAC_256,WLAN_CIPHER_SUITE_BIP_GMAC_128,WLAN_CIPHER_SUITE_BIP_GMAC_256};
struct ieee80211_key {unsigned flags;struct {unsigned cipher,flags;}conf;};
struct ieee80211_hdr {u16 frame_control,duration;u8 addr1[6],addr2[6],addr3[6];u16 seq;u8 qos[2];} __attribute__((packed));
struct ethhdr {u8 h_dest[6],h_source[6];u16 h_proto;} __attribute__((packed));
struct ieee80211_tx_info {unsigned flags;struct {unsigned flags;void *hw_key;}control;};
struct sk_buff {u8 memory[256],*data;unsigned len;u16 protocol;struct ieee80211_tx_info info;};
struct sta_info {unsigned flags,ptk_idx;struct ieee80211_key *ptk[4];};
struct ieee80211_link_data {struct ieee80211_key *default_multicast_key,*default_mgmt_key;};
struct ieee80211_local {struct {unsigned extra_tx_headroom;}hw;};
struct ieee80211_sub_if_data {struct {unsigned type;u8 addr[6];struct {u8 ap_addr[6];}cfg;}vif;
 struct {bool use_4addr;struct {unsigned flags;}*wiphy;}wdev;
 union {struct {struct sta_info *sta;}vlan;}u;struct ieee80211_local *local;
 struct ieee80211_link_data deflink,*link[16];struct ieee80211_key *default_unicast_key;u16 control_port_protocol;};
struct ieee80211_tx_data {struct ieee80211_sub_if_data *sdata;struct sk_buff *skb;struct sta_info *sta;struct ieee80211_key *key;};
struct ieee80211_fast_tx {unsigned hdr_len,sa_offs,da_offs;};
static const u8 rfc1042_header[6]={0xaa,0xaa,3,0,0,0};
static bool is_multicast_ether_addr(const u8 *p){return p[0]&1;}
static bool test_sta_flag(struct sta_info *s,unsigned bit){return s->flags&BIT(bit);}
static bool ieee80211_is_data_present(u16 fc){return (fc&12)==8;}
static bool ieee80211_is_mgmt(u16 fc){return !(fc&12);}
static bool ieee80211_is_deauth(u16 fc){return (fc&0xfc)==0xc0;}
static bool ieee80211_has_fromds(u16 fc){return fc&0x200;}
static bool ieee80211_has_tods(u16 fc){return fc&0x100;}
static bool ieee80211_is_group_privacy_action(struct sk_buff *s){(void)s;return false;}
static bool ieee80211_is_robust_mgmt_frame(struct sk_buff *s){(void)s;return false;}
static bool ieee80211_use_mfp(u16 f,struct sta_info *s,struct sk_buff *b){(void)f;(void)s;(void)b;return false;}
static bool ieee80211_is_tdls_setup(struct sk_buff *s){(void)s;return false;}
static u8 *ieee80211_get_qos_ctl(struct ieee80211_hdr *h){return h->qos;}
static unsigned u32_get_bits(unsigned v,unsigned m){return v&m;}
#define IEEE80211_SKB_CB(s) (&(s)->info)
static struct sta_info associated;
static bool absent;static unsigned lookups;
static struct sta_info *sta_info_get(struct ieee80211_sub_if_data *s,const u8 *a){assert(!memcmp(a,s->vif.cfg.ap_addr,6));lookups++;return absent?NULL:&associated;}
static struct sta_info *sta_info_get_bss(struct ieee80211_sub_if_data *s,const u8 *a){(void)s;(void)a;return NULL;}
static void ether_addr_copy(u8 *d,const u8 *s){memcpy(d,s,6);}
static void *skb_push(struct sk_buff *s,unsigned n){assert(s->data>=s->memory+n);s->data-=n;s->len+=n;return s->data;}
static bool ieee80211_amsdu_realloc_pad(struct ieee80211_local *l,struct sk_buff *s,unsigned n){(void)l;assert(s->data>=s->memory+n);return true;}
'''
for name in ('ieee80211_lookup_ra_sta','ieee80211_select_link_key','ieee80211_tx_h_select_key','ieee80211_amsdu_prepare_head'):
    code+=function(name)
# Execute the exact normal three-address branch; unrelated TDLS/MLO/mesh
# construction is deliberately outside this scoped packet comparison.
build=function('ieee80211_build_hdr')
start=build.index('/* BSSID SA DA */',build.index('case NL80211_IFTYPE_STATION:'))
end=build.index('hdrlen = 24;',start)+len('hdrlen = 24;')
code+='static struct ieee80211_hdr header(struct ieee80211_sub_if_data *sdata,struct sk_buff *skb){struct ieee80211_hdr hdr={.frame_control=0x4188};unsigned hdrlen;\n'+build[start:end]+'\nassert(hdrlen==24);return hdr;}\n'
code+=r'''
int main(void){
 struct ieee80211_local local={};struct ieee80211_sub_if_data s={.vif={.type=NL80211_IFTYPE_STATION,.addr={2,0,0,0,0,1},.cfg={{2,0,0,0,0,2}}},.local=&local};
 typeof(*s.wdev.wiphy) wiphy={};s.wdev.wiphy=&wiphy;
 struct ieee80211_key ptk={.flags=KEY_FLAG_UPLOADED_TO_HARDWARE,.conf={WLAN_CIPHER_SUITE_CCMP,0}},gtk=ptk;
 associated.flags=BIT(WLAN_STA_USES_ENCRYPTION);associated.ptk[0]=&ptk;s.deflink.default_multicast_key=&gtk;
 const u8 dests[3][6]={{1,0,0x5e,1,2,3},{0x33,0x33,0,1,2,3},{2,0,0,0,0,3}};
 for(unsigned d=0;d<3;d++)for(unsigned source=0;source<2;source++) {
  struct sk_buff skb={};skb.data=skb.memory+64;skb.len=60;
  memcpy(skb.data,dests[d],6);memcpy(skb.data+6,s.vif.addr,6);if(source)skb.data[11]=9;
  skb.data[12]=8;skb.data[13]=0;
  struct sta_info *sta=NULL;assert(!ieee80211_lookup_ra_sta(&s,&skb,&sta)&&sta==&associated);
  struct ieee80211_hdr h=header(&s,&skb);
  assert(!memcmp(h.addr1,s.vif.cfg.ap_addr,6)&&!memcmp(h.addr2,skb.data+6,6)&&!memcmp(h.addr3,dests[d],6));
  u8 saved_sa[6];memcpy(saved_sa,h.addr2,6);
  memcpy(skb.data,&h,26);skb.len=60;skb.info.control.flags=IEEE80211_LINK_UNSPECIFIED;
  struct ieee80211_tx_data tx={.sdata=&s,.skb=&skb,.sta=sta};
  assert(ieee80211_tx_h_select_key(&tx)==TX_CONTINUE&&tx.key==&ptk&&skb.info.control.hw_key==&ptk.conf);
  struct ieee80211_fast_tx fast={.hdr_len=32,.sa_offs=10,.da_offs=16};skb.info.control.flags=0;
  assert(ieee80211_amsdu_prepare_head(&s,&fast,&skb));
  struct ieee80211_hdr *aggregate=(void *)skb.data;struct ethhdr *subframe=(void *)(skb.data+26);
  assert(!memcmp(aggregate->addr1,s.vif.cfg.ap_addr,6)&&!memcmp(aggregate->addr2,saved_sa,6)&&!memcmp(aggregate->addr3,s.vif.cfg.ap_addr,6));
  assert(!memcmp(subframe->h_dest,dests[d],6)&&!memcmp(subframe->h_source,saved_sa,6)&&aggregate->qos[0]&0x80);
 }
 struct sk_buff skb={.data=(u8 *)dests[0]};struct sta_info *sta=NULL;absent=true;
 assert(ieee80211_lookup_ra_sta(&s,&skb,&sta)==-ENOLINK);
 puts("Linux STA group:6 actual receiver/key/header/AMSDU cases, original Ethernet SA/DA and missing-AP refusal passed");
}
'''
with tempfile.TemporaryDirectory(prefix='sta-group-linux-') as t:
    p=Path(t);(p/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
