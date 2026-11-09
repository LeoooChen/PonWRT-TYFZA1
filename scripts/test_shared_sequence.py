#!/usr/bin/env python3
"""Execute ARM/PE interface sequence allocation and the real Linux TX handler."""
import argparse
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--wifi',type=Path,required=True)
args=parser.parse_args()
sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
def boundary(name,values):
    raise AssertionError((name,values))
stock=ModuleOracle(args.wifi,['rtw_get_sw_seq'],boundary)
stock.uc.mem_write(stock.symbols['rtw_drv_log_level'][0],bytes(4))
station,attrib=stock.scratch+0x1000,stock.scratch+0x2000
cases=0
for last in (0,1,255,4094,4095):
    for qos in (False,True):
        for tid in range(8):
            stock.uc.mem_write(station,bytes(1024));stock.uc.mem_write(attrib,bytes(128))
            stock.uc.mem_write(attrib+80,bytes([qos]));stock.uc.mem_write(attrib+89,bytes([tid]))
            bank=tid if qos else 9
            stock.uc.mem_write(station+600+bank*2,struct.pack('<H',last))
            for n in range(3):
                expected=(last+n+1)&4095
                assert stock.call('rtw_get_sw_seq',0,station,attrib)==expected
                assert stock.unpack(station+600+bank*2,'H')[0]==expected
                cases+=1
print(f'Original sequence: {cases} non-QoS bank9 / QoS TID and wrap comparisons passed')

overlay=root/'port/overlay/target/linux/rtl9617c/files-6.18'
header=(overlay/'include/linux/rtl9617c-seq.h').read_text().replace('#include <linux/types.h>','')
header=header.replace('unsigned int generation, arm, pe, turn, next;',
                      '_Atomic unsigned int generation, arm, pe, turn, next;')
core=re.sub(r'^#include.*\n','',(overlay/'net/core/rtl9617c-seq.c').read_text(),flags=re.M)
peer=re.sub(r'^#include.*\n','',(root/'port/pe/rtl9617c-radio-seq.h').read_text(),flags=re.M)
kernel=next((root/'sources/openwrt/build_dir').glob('target-*/linux-rtl9617c_generic/linux-6.18.55'))
text=(kernel/'net/mac80211/tx.c').read_text()
def function(text,name):
    begin=re.search(r'^[^\n]*\b'+name+r'\([^;]*?\)\s*\{',text,re.M).start()
    end=text.index('{',begin)+1;depth=1
    while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[begin:end]+'\n'
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <sched.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define __KERNEL__
#define CONFIG_ARCH_RTL9617C 1
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define mb() atomic_thread_fence(memory_order_seq_cst)
#define sync() mb()
#define udelay(n) ((void)n,sched_yield())
#define DEFINE_SPINLOCK(n) pthread_mutex_t n=PTHREAD_MUTEX_INITIALIZER
#define spin_lock_irqsave(p,f) ((f)=0,assert(!pthread_mutex_lock(p)))
#define spin_unlock_irqrestore(p,f) ((void)(f),assert(!pthread_mutex_unlock(p)))
#define EXPORT_SYMBOL_GPL(n)
'''+header+core+peer+r'''
#define BIT(n) (1U<<(n))
#define TX_CONTINUE 0
#define TX_DROP 1
#define NL80211_IFTYPE_MONITOR 6
#define NL80211_IFTYPE_AP 3
#define IEEE80211_NUM_TIDS 16
#define IEEE80211_TX_CTRL_NO_SEQNO BIT(0)
#define IEEE80211_TX_CTRL_MCAST_MLO_FIRST_TX BIT(1)
#define IEEE80211_TX_CTL_ASSIGN_SEQ BIT(2)
#define cpu_to_le16(v) (v)
#define unlikely(v) (v)
struct ieee80211_hdr {u16 frame_control;u8 addr1[6];u16 seq_ctrl;};
struct rtl_ax_role_identity {u8 port,mbid,macid,type;};
struct rtl_ax_vif {struct rtl_ax_role_identity identity;};
struct ieee80211_vif {unsigned type;bool mld;struct rtl_ax_vif drv_priv[1];};
struct ieee80211_tx_info {unsigned flags;struct {struct ieee80211_vif *vif;unsigned flags;}control;};
struct sk_buff {unsigned priority;u8 *data;struct ieee80211_tx_info info;};
struct ieee80211_sub_if_data {struct ieee80211_vif vif;struct {struct {void *wiphy;}hw;}*local;struct rtl_seq_binding rtl9617c_sequence;u16 sequence_number,mld_mcast_seq;};
struct sta_info {struct {struct {unsigned msdu[17];}tx_stats;}deflink;};
struct ieee80211_tx_data {struct sk_buff *skb;struct ieee80211_sub_if_data *sdata;struct sta_info *sta;};
#define IEEE80211_SKB_CB(s) (&(s)->info)
static bool ieee80211_is_ctl(u16 f){return (f&12)==4;}
static unsigned ieee80211_hdrlen(u16 f){return ieee80211_is_ctl(f)?16:24;}
static bool ieee80211_is_qos_nullfunc(u16 f){return (f&0xfc)==0xc8;}
static bool ieee80211_is_data_qos(u16 f){return (f&0x8c)==0x88;}
static bool ieee80211_vif_is_mld(struct ieee80211_vif *v){return v->mld;}
static bool is_multicast_ether_addr(const u8 *a){return a[0]&1;}
static int ieee80211_get_tid(struct ieee80211_hdr *h){(void)h;return 3;}
static u16 ieee80211_tx_next_seq(struct sta_info *s,int tid){(void)s;assert(tid==3);return 0x1230;}
static unsigned shared_sequence_handler(struct ieee80211_tx_data *tx);
'''
code+=r'''
static struct ieee80211_sub_if_data *vif_to_sdata(struct ieee80211_vif *v){return (void *)v;}
#define lockdep_assert_wiphy(p) ((void)(p))
#define NL80211_IFTYPE_STATION 2
'''
wrappers=(overlay/'net/mac80211/rtl9617c-mcast.h').read_text()
for name in ('ieee80211_rtl9617c_seq_bind','ieee80211_rtl9617c_seq_unbind','ieee80211_rtl9617c_next_sequence'):
    code+=function(wrappers,name)
code+=r'''
struct rtl_ax_tx {bool pe_active;u32 sequence_generation;struct {void *memory;}pe;};
static unsigned revoked;
static void rtl_ax_tx_mcast_revoke(struct rtl_ax_tx *tx){(void)tx;revoked++;}
'''
radio=(overlay/'drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()
for name in ('rtl_ax_tx_sequence_bind','rtl_ax_tx_sequence_unbind'):
    code+=function(radio,name)
handler=function(text,'ieee80211_tx_h_sequence')
code+='static unsigned '+handler.replace('ieee80211_tx_h_sequence(', 'shared_sequence_handler(')
# Execute the real fast path's sequence block, stopping before its unrelated
# statistics, encryption and aggregation work.
fast=function(text,'ieee80211_xmit_fast_finish')
fast=fast[fast.index('\tif (hdr->frame_control &'):fast.index('\tif (skb_shinfo(skb)->gso_size)')]
code+='''
#define IEEE80211_STYPE_QOS_DATA 0x80
#define IEEE80211_QOS_CTL_TAG1D_MASK 7
static unsigned fast_sequence(struct ieee80211_sub_if_data *sdata,struct sta_info *sta,struct sk_buff *skb){
 struct ieee80211_tx_info *info=IEEE80211_SKB_CB(skb);struct ieee80211_hdr *hdr=(void *)skb->data;u8 tid;
'''+fast+'return TX_CONTINUE;}\n'
code+=r'''
#define COUNT 8192
static u8 memory[0x80000] __attribute__((aligned(64)));
static struct rtl_seq_binding binding;
static u16 software;
static _Atomic unsigned histogram[4096];
static void *sender(void *arg){
 for(unsigned n=0;n<COUNT;) {
  u32 seq;u16 field;int ret;
  if((uintptr_t)arg==2){ret=pe_sequence_next(memory,1,&seq);assert(ret!=2);if(!ret)continue;}
  else {ret=rtl9617c_seq_next(&binding,&software,&field);if(ret==-ETIMEDOUT)continue;assert(!ret);seq=field>>4;}
  assert(seq<4096);histogram[seq]++;n++;
 }
 return NULL;
}
int main(void){
 struct rtl_seq_shared *s=(void *)(memory+RTL_SEQ_BASE);pthread_t workers[3];
 assert(!rtl9617c_seq_bind(&binding,s,1,&software));
 for(unsigned i=0;i<3;i++)assert(!pthread_create(workers+i,NULL,sender,(void *)(uintptr_t)i));
 for(unsigned i=0;i<3;i++)assert(!pthread_join(workers[i],NULL));
 for(unsigned i=0;i<4096;i++)assert(histogram[i]==6);
 assert(!s->next&&!rtl9617c_seq_unbind(&binding,&software)&&!software);
 struct ieee80211_sub_if_data sd={};struct ieee80211_vif vif={.type=3};struct sta_info sta={};
 sd.vif.type=3;
 assert(!ieee80211_rtl9617c_seq_bind(&sd.vif,s,2));
 /* Linux management, non-QoS data and group QoS share the same sequence;
  * NI alternates in that space. Linux runs before fragmentation. */
 for(unsigned n=0;n<4100;n++) {
  struct ieee80211_hdr h={.frame_control=n%3==0?0:n%3==1?8:0x88,.addr1={1}};
  struct sk_buff skb={.data=(u8 *)&h,.info={.control={.vif=&vif}}};
  struct ieee80211_tx_data tx={.skb=&skb,.sdata=&sd,.sta=&sta};u32 seq;
  assert(!shared_sequence_handler(&tx)&&h.seq_ctrl==((n*2)&4095)<<4);
  assert(skb.info.flags&IEEE80211_TX_CTL_ASSIGN_SEQ);
  assert(pe_sequence_next(memory,2,&seq)==1&&seq==((n*2+1)&4095));
  /* Subsequent fragments keep the assigned upper12 bits. */
  for(unsigned f=0;f<4;f++)assert(((h.seq_ctrl|f)>>4)==((n*2)&4095));
 }
 for(unsigned mode=0;mode<5;mode++){
  struct ieee80211_hdr h={.frame_control=mode==0?4:mode==1?0xc8:mode==4?0x88:8,.seq_ctrl=0x770};
  struct sk_buff skb={.data=(u8 *)&h,.info={.control={.vif=&vif,.flags=mode==2?1:0}}};
  struct ieee80211_tx_data tx={.skb=&skb,.sdata=&sd,.sta=&sta};u32 before=s->next;
  vif.type=mode==3?6:3;assert(!shared_sequence_handler(&tx));
  assert(s->next==before&&h.seq_ctrl==(mode==4?0x1230:0x770));
 }
 for(unsigned n=0;n<32;n++){
  struct ieee80211_hdr h={.frame_control=8};struct sk_buff skb={.data=(u8 *)&h};
  u32 before=s->next;u16 field;
  assert(!fast_sequence(&sd,&sta,&skb)&&h.seq_ctrl==before<<4);
  assert(!ieee80211_rtl9617c_next_sequence(&sd.vif,&field)&&field==((before+1)&4095)<<4);
 }
 assert(!ieee80211_rtl9617c_seq_unbind(&sd.vif));
 u32 seq;assert(pe_sequence_next(memory,2,&seq)==2);
 assert(!rtl9617c_seq_bind(&sd.rtl9617c_sequence,s,3,&sd.sequence_number));
 assert(pe_sequence_next(memory,2,&seq)==2);s->pe=1;s->turn=1;
 assert(rtl9617c_seq_unbind(&sd.rtl9617c_sequence,&sd.sequence_number)==-ETIMEDOUT&&!sd.rtl9617c_sequence.shared);
 assert(fast_sequence(&sd,&sta,&(struct sk_buff){.data=(u8 *)&(struct ieee80211_hdr){.frame_control=8}})==TX_DROP);
 sd.vif.mld=true;assert(ieee80211_rtl9617c_seq_bind(&sd.vif,s,4)==-EOPNOTSUPP);
 u16 result;assert(rtl9617c_seq_next(&sd.rtl9617c_sequence,&sd.sequence_number,&result)==-ESTALE);
 struct ieee80211_sub_if_data fresh={.vif={.type=3}};struct rtl_ax_tx radio={.pe_active=true,.pe={memory}};
 s->pe=0;s->arm=0;s->generation=0;fresh.sequence_number=0xffe0;
 assert(!rtl_ax_tx_sequence_bind(&radio,&fresh.vif)&&s->generation==1&&s->next==4094);
 assert(!ieee80211_rtl9617c_next_sequence(&fresh.vif,&result)&&result==0xffe0);
 assert(!rtl_ax_tx_sequence_unbind(&radio,&fresh.vif)&&revoked==1&&fresh.sequence_number==0xfff0);
 radio.sequence_generation=RTL_SEQ_GENERATION_MASK;
 assert(rtl_ax_tx_sequence_bind(&radio,&fresh.vif)==-EOVERFLOW&&!s->generation);
 radio.pe_active=false;assert(!rtl_ax_tx_sequence_bind(&radio,&fresh.vif)&&!s->generation);
 fresh.vif.type=7;radio.pe_active=true;assert(!rtl_ax_tx_sequence_bind(&radio,&fresh.vif));
 /* Bind all12 actual interface domains together and interleave Linux/PE. */
 struct ieee80211_sub_if_data interfaces[RTL_SEQ_DOMAINS]={};
 struct rtl_ax_tx multi={.pe_active=true,.pe={memory}};memset(memory,0,sizeof(memory));
 for(unsigned i=0;i<RTL_SEQ_DOMAINS;i++){
  interfaces[i].vif.type=NL80211_IFTYPE_AP;
  interfaces[i].vif.drv_priv[0].identity.port=i<8?0:i-7;
  interfaces[i].vif.drv_priv[0].identity.mbid=i<8?i:0;
  interfaces[i].sequence_number=(i*300)<<4;
  assert(!rtl_ax_tx_sequence_bind(&multi,&interfaces[i].vif));
 }
 for(unsigned n=0;n<4100;n++)for(unsigned i=0;i<RTL_SEQ_DOMAINS;i++){
  unsigned role=i<8?i:(i-7)<<4,token=rtl_seq_token(memory,role),seq;
  assert(token==((i<<RTL_SEQ_DOMAIN_SHIFT)|(i+1)));
  assert(!ieee80211_rtl9617c_next_sequence(&interfaces[i].vif,&result));
  assert(result==((i*300+n*2)&4095)<<4);
  assert(pe_sequence_next(memory,token,&seq)==1&&seq==((i*300+n*2+1)&4095));
 }
 for(unsigned i=0;i<RTL_SEQ_DOMAINS;i++){
  unsigned role=i<8?i:(i-7)<<4,old=rtl_seq_token(memory,role),seq;
  assert(!rtl_ax_tx_sequence_unbind(&multi,&interfaces[i].vif));
  assert(!rtl_seq_current(memory,old)&&pe_sequence_next(memory,old,&seq)==2);
  assert(!rtl_ax_tx_sequence_bind(&multi,&interfaces[i].vif));
  assert(!rtl_seq_current(memory,old));
 }
 assert(!rtl_seq_token(memory,0x11)&&!rtl_seq_token(memory,0x50));
 struct ieee80211_sub_if_data invalid={.vif={.type=NL80211_IFTYPE_AP,.drv_priv={{{.port=1,.mbid=1}}}}};
 assert(rtl_ax_tx_sequence_bind(&multi,&invalid.vif)==-EINVAL);
 assert(!rtl_seq_current(memory,1U<<RTL_SEQ_DOMAIN_SHIFT));
 assert(rtl9617c_seq_bind(&invalid.rtl9617c_sequence,s,RTL_SEQ_GENERATION_MASK+1,&invalid.sequence_number)==-EINVAL);
 puts("Per-VIF sequences: 12 concurrent interface bindings, 98400 Linux/PE alternations, independent rebind and invalid-domain rejection passed");
 puts("Shared sequence:24576 concurrent allocations,4100 actual Linux/PE alternations, wrap, control/QoS exclusions, interface replacement and timeout passed");
}
'''
with tempfile.TemporaryDirectory(prefix='shared-sequence-') as tmp:
    tmp=Path(tmp);(tmp/'test.c').write_text(code)
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined',str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True)
