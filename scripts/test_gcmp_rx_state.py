#!/usr/bin/env python3
"""Execute actual Linux RX shortcut PN state and native radio cipher publication."""
from pathlib import Path
import re, subprocess, tempfile

root=Path(__file__).resolve().parents[1]
kernel=next((root/'sources/openwrt/build_dir').glob('target-*/linux-rtl9617c_generic/linux-6.18.55'))
overlay=root/'port/overlay/target/linux/rtl9617c/files-6.18'
def function(source,name):
 a=re.search(r'^(?:static )?[^\n]*\b'+name+r'\([^;]*?\)\s*\{',source,re.M).start()
 b=source.index('{',a)+1;depth=1
 while depth:depth+=(source[b]=='{')-(source[b]=='}');b+=1
 return source[a:b]+'\n'
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define WLAN_CIPHER_SUITE_CCMP 4
#define WLAN_CIPHER_SUITE_GCMP 8
#define WLAN_CIPHER_SUITE_CCMP_256 10
#define WLAN_CIPHER_SUITE_GCMP_256 9
#define WLAN_STA_AUTHORIZED 1
#define WLAN_STA_USES_ENCRYPTION 2
#define rcu_access_pointer(p) (p)
#define container_of(p,t,m) ((t*)((char*)(p)-offsetof(t,m)))
#define rcu_dereference(p) (p)
#define cpu_to_le16(v) (v)
#define EXPORT_SYMBOL_GPL(n)
struct ieee80211_sta {unsigned dummy;};
struct ieee80211_local {int rx_path_lock;};
struct ieee80211_sub_if_data {struct ieee80211_local *local;struct ieee80211_key *default_unicast_key;};
struct tid_ampdu_rx {unsigned stored_mpdu_num,head_seq_num;int reorder_lock;};
struct sta_info {struct ieee80211_sta sta;struct ieee80211_sub_if_data *sdata;bool authorized,encrypted;struct ieee80211_key *ptk[4];unsigned ptk_idx;u16 rtl9617c_open_next[16],rtl9617c_open_valid;
 struct {struct tid_ampdu_rx *tid_rx[16];}ampdu_mlme;u16 last_seq_ctrl[16];};
struct ieee80211_key_conf {unsigned cipher,keyidx;};
/* Separate storage catches use of CCMP member for a GCMP key even though
 * today's kernel union happens to place both PN arrays at the same offset. */
struct ieee80211_key {struct ieee80211_key_conf conf;struct sta_info *sta;u16 rx_shortcut_valid_tids;
 u16 rx_shortcut_next_seq[16];struct {struct {u8 rx_pn[17][6];}ccmp,gcmp;}u;};
static unsigned markers;
static void spin_lock_bh(int *p){assert(!*p);*p=1;}
static void spin_unlock_bh(int *p){assert(*p);*p=0;}
static bool test_sta_flag(struct sta_info *s,unsigned f){return f==1?s->authorized:s->encrypted;}
static void ieee80211_mark_rx_ba_filtered_frames(struct ieee80211_sta *s,u8 tid,u16 next,u32 filtered,u16 count){
 (void)s;(void)next;assert(tid<16&&!filtered&&count==1);markers++;
}
'''
source=(kernel/'net/mac80211/rx.c').read_text()
mark_start=source.index('\t\tif (rx->sta && !rx->key && !test_sta_flag')
mark_end=source.index('\t\tCALL_RXH(ieee80211_rx_h_defragment);',mark_start)
code += r'''
#define IEEE80211_SCTL_FRAG 15
#define le16_to_cpu(v) (v)
struct ieee80211_hdr {u16 frame_control,seq_ctrl;};
struct sk_buff {void *data;};
struct ieee80211_rx_data {struct sta_info *sta;struct ieee80211_key *key;struct sk_buff *skb;unsigned security_idx;};
static bool ieee80211_is_data_qos(u16 fc){return (fc&0xfc)==0x88;}
static bool ieee80211_has_protected(u16 fc){return fc&0x4000;}
static bool ieee80211_has_morefrags(u16 fc){return fc&0x400;}
static void accepted(struct ieee80211_rx_data *rx){
''' + source[mark_start:mark_end] + '}\n'
code+=function(source,'ieee80211_rx_shortcut_snapshot')+function(source,'ieee80211_rx_shortcut_sync')
code+=r'''
struct rtl_ax_rx {u8 *uplink_states,*rpq_memory;int uplink_lock;unsigned uplink_wifi_id;
 bool uplink_open[128];struct ieee80211_key_conf *keys[128];u8 uplink_trigger_tids[128];u16 uplink_pending_tids[128];
 unsigned uplink_seen[128][16];struct {u8 *memory;}pe;struct ieee80211_hw *hw;};
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
#define dma_wmb() ((void)0)
'''
radio=(overlay/'drivers/net/wireless/realtek/rtl_ax_ap/rx-offload-state.h').read_text()
code+=function(radio,'rtl_ax_rx_key')+function(radio,'rtl_ax_rx_open_peer')+function(radio,'rtl_ax_rx_uplink_enable')
code+=r'''
int main(void){
 struct ieee80211_local l={};struct ieee80211_sub_if_data s={.local=&l};struct sta_info sta={.sdata=&s,.authorized=true};
 unsigned cases=0;
 for(unsigned sequence=0;sequence<4096;sequence+=4095)for(unsigned fault=0;fault<6;fault++) {
  struct ieee80211_hdr hdr={0x188,sequence<<4};struct sk_buff skb={&hdr};struct ieee80211_key key={};
  struct ieee80211_rx_data rx={.sta=&sta,.skb=&skb,.security_idx=3};sta.rtl9617c_open_valid=0;sta.encrypted=false;
  if(fault==1)rx.key=&key;if(fault==2)sta.encrypted=true;if(fault==3)hdr.frame_control|=0x4000;
  if(fault==4)hdr.frame_control|=0x400;if(fault==5)hdr.seq_ctrl|=1;
  accepted(&rx);assert(!!sta.rtl9617c_open_valid==(fault==0));
  if(!fault)assert(sta.rtl9617c_open_next[3]==((sequence+1)&4095));
 }
 sta.encrypted=false;
 for(unsigned family=0;family<4;family++)for(unsigned tid=0;tid<16;tid++)for(unsigned ba_mode=0;ba_mode<4;ba_mode++) {
  unsigned alg=(unsigned[]){4,8,9,10}[family];
  struct ieee80211_key key={.conf={alg,2},.sta=&sta,.rx_shortcut_valid_tids=BIT(tid)};
  struct tid_ampdu_rx ba={.head_seq_num=4095};sta.ampdu_mlme.tid_rx[tid]=ba_mode?&ba:NULL;
  if(ba_mode==2)ba.stored_mpdu_num=1;if(ba_mode==3)ba.head_seq_num=0;
  key.rx_shortcut_next_seq[tid]=4095;
  u8 *pn=(alg==8||alg==9)?key.u.gcmp.rx_pn[tid]:key.u.ccmp.rx_pn[tid];
  u8 *other=(alg==8||alg==9)?key.u.ccmp.rx_pn[tid]:key.u.gcmp.rx_pn[tid];
  memset(other,0xa5,6);pn[5]=10;u8 out[6];u16 next=123;
  bool ready=ieee80211_rx_shortcut_snapshot(&sta.sta,&key.conf,tid,&next,out);
  assert(ready==(ba_mode<2)&&!l.rx_path_lock&&!ba.reorder_lock);
  if(ready)assert(next==4095&&!memcmp(out,pn,6));
  u8 newer[6]={0,0,0,0,1,0};ieee80211_rx_shortcut_sync(&sta.sta,&key.conf,tid,0,newer,1);
  assert(!memcmp(pn,newer,6)&&key.rx_shortcut_next_seq[tid]==0&&sta.last_seq_ctrl[tid]==65520);
  u8 older[6]={0,0,0,0,0,11};ieee80211_rx_shortcut_sync(&sta.sta,&key.conf,tid,1,older,1);
  assert(!memcmp(pn,newer,6));for(unsigned i=0;i<6;i++)assert(other[i]==0xa5);
  sta.authorized=false;assert(!ieee80211_rx_shortcut_snapshot(&sta.sta,&key.conf,tid,&next,out));sta.authorized=true;
  cases++;
 }
 for(unsigned tid=0;tid<16;tid++)for(unsigned mode=0;mode<7;mode++) {
  struct ieee80211_key unused={};struct tid_ampdu_rx ba={.head_seq_num=4095};
  sta.encrypted=false;sta.ptk[0]=NULL;s.default_unicast_key=NULL;sta.authorized=true;
  sta.rtl9617c_open_valid=BIT(tid);sta.rtl9617c_open_next[tid]=4095;
  sta.ampdu_mlme.tid_rx[tid]=&ba;
  if(mode==1)sta.encrypted=true;if(mode==2)sta.ptk[0]=&unused;if(mode==3)s.default_unicast_key=&unused;
  if(mode==4)sta.authorized=false;if(mode==5)ba.stored_mpdu_num=1;if(mode==6)sta.rtl9617c_open_valid=0;
  u8 pn[6]={1,2,3,4,5,6};u16 next=123;
  assert(ieee80211_rx_shortcut_snapshot(&sta.sta,NULL,tid,&next,pn)==(mode==0));
  if(mode==0) {
   assert(next==4095&&!memcmp(pn,(u8[6]){},6));
   ieee80211_rx_shortcut_sync(&sta.sta,NULL,tid,0,pn,1);
   assert(sta.last_seq_ctrl[tid]==65520&&sta.rtl9617c_open_next[tid]==0);
  }
 }
 static u8 states[0x10200],tx[0x20000];struct rtl_ax_rx rx={.uplink_states=states,.rpq_memory=tx,.uplink_wifi_id=7};
 struct ieee80211_key_conf key={4,2};
 for(unsigned i=0;i<4;i++) {
  key.cipher=(unsigned[]){4,8,9,10}[i];rtl_ax_rx_key(&rx,7,&key);
  assert(!get_unaligned_le32(states+0x1001c));
  rx.uplink_trigger_tids[7]=0xa5;rtl_ax_rx_uplink_enable(&rx,7);
  assert(get_unaligned_le32(states+0x1001c)==(BIT(31)|7<<8|2|0xa5<<16|(i?BIT(24):0)));
 }
 rtl_ax_rx_key(&rx,7,NULL);rtl_ax_rx_open_peer(&rx,7,true);rtl_ax_rx_uplink_enable(&rx,7);
 assert(get_unaligned_le32(states+0x1001c)==(BIT(31)|BIT(25)|7<<8|0xa5<<16));
 rtl_ax_rx_key(&rx,7,&key);assert(!rx.uplink_open[7]&&!get_unaligned_le32(states+0x1001c));
 printf("RX cipher state: %u actual Linux PN/BA/authorization cases and four native cipher transitions passed\n",cases);
}
'''
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);(p/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
