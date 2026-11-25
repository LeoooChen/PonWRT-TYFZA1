#!/usr/bin/env python3
"""Compile actual mac80211 PS budget and queue paths with bounded fixtures."""
from pathlib import Path
import subprocess,tempfile
import argparse
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('kernel', type=Path)
args=parser.parse_args()
d=args.kernel.resolve()/'net/mac80211'
s=(d/'ieee80211_i.h').read_text();a=s.index('#define IEEE80211_PS_STA_BYTES');b=s.index('static inline void ieee80211_ps_uncharge_queue',a)
code=r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <sched.h>
#include <stdio.h>
typedef atomic_int atomic_t;
#define atomic_read(p) atomic_load(p)
#define atomic_sub(n,p) atomic_fetch_sub(p,n)
static int atomic_cmpxchg(atomic_t*p,int old,int n){atomic_compare_exchange_strong(p,&old,n);return old;}
struct ieee80211_local { atomic_t ps_buffered_bytes; };
struct sta_info { atomic_t ps_buffered_bytes; };
struct sk_buff { unsigned int truesize; };
'''+s[a:b]+r'''
static struct ieee80211_local radio;
static struct sta_info sta[8];
static void *worker(void *arg) {
 uintptr_t id=(uintptr_t)arg;
 struct sk_buff skb={.truesize=8192+(unsigned)id*512};
 for(unsigned i=0;i<50000;i++) {
  if(ieee80211_ps_charge(&radio,&sta[id],&skb)) {
   assert(atomic_read(&radio.ps_buffered_bytes)<=IEEE80211_PS_RADIO_BYTES);
   assert(atomic_read(&sta[id].ps_buffered_bytes)<=IEEE80211_PS_STA_BYTES);
   if(!(i%16))sched_yield();
   ieee80211_ps_uncharge(&radio,&sta[id],&skb);
  }
 }
 return 0;
}
int main(void) {
 struct sk_buff unit={.truesize=4096},huge={.truesize=IEEE80211_PS_STA_BYTES+1};
 assert(!ieee80211_ps_charge(&radio,&sta[0],&huge));
 assert(!atomic_read(&radio.ps_buffered_bytes));
 for(unsigned i=0;i<2048;i++)assert(ieee80211_ps_charge(&radio,&sta[0],&unit));
 assert(!ieee80211_ps_charge(&radio,&sta[0],&unit));
 for(unsigned i=0;i<2048;i++)assert(ieee80211_ps_charge(&radio,&sta[1],&unit));
 assert(!ieee80211_ps_charge(&radio,&sta[2],&unit));
 assert(!ieee80211_ps_charge(&radio,0,&unit));
 for(unsigned j=0;j<2;j++)for(unsigned i=0;i<2048;i++)ieee80211_ps_uncharge(&radio,&sta[j],&unit);
 assert(!atomic_read(&radio.ps_buffered_bytes));
 assert(ieee80211_ps_charge(&radio,0,&unit));ieee80211_ps_uncharge(&radio,0,&unit);
 pthread_t t[8];for(uintptr_t i=0;i<8;i++)assert(!pthread_create(&t[i],0,worker,(void*)i));
 for(unsigned i=0;i<8;i++){pthread_join(t[i],0);assert(!atomic_read(&sta[i].ps_buffered_bytes));}
 assert(!atomic_read(&radio.ps_buffered_bytes));
 puts("PS byte budget: station/global/oversize/rollback and400kconcurrent charges PASS");
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.c').write_text(code);subprocess.run(['cc','-std=gnu11','-O2','-pthread','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(p/'test.c'),'-o',str(p/'test')],check=True);subprocess.run([str(p/'test')],check=True)

from pathlib import Path
import re,subprocess,tempfile

def func(s,name):
 m=re.search(r'^(?:static )?(?:inline )?(?:void|bool|ieee80211_tx_result)\s*\n?'+name+r'\(',s,re.M);assert m,name
 a=m.start();b=s.index('{',a)+1;depth=1
 while depth:depth+=(s[b]=='{')-(s[b]=='}');b+=1
 return s[a:b]+'\n'
h=(d/'ieee80211_i.h').read_text();h=h[h.index('#define IEEE80211_PS_STA_BYTES'):h.index('#endif /* IEEE80211_I_H */')]
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef unsigned char u8;typedef unsigned short u16;typedef int atomic_t;
#define atomic_read(p) (*(p))
#define atomic_sub(n,p) (*(p)-=(n))
static int atomic_cmpxchg(atomic_t*p,int old,int n){int v=*p;if(v==old)*p=n;return v;}
#define IEEE80211_NUM_ACS 4
#define IEEE80211_AC_BK 3
#define WLAN_STA_PS_STA 1
#define WLAN_STA_PS_DRIVER 2
#define WLAN_STA_PS_DELIVER 4
#define IEEE80211_TX_CTL_NO_PS_BUFFER 1
#define IEEE80211_TX_INTCFL_NEED_TXPROCESSING 2
#define IEEE80211_TX_TEMPORARY_FLAGS 8
#define unlikely(x) (x)
#define STA_INFO_CLEANUP_INTERVAL 10
#define round_jiffies(x) (x)
#define test_sta_flag(s,f) ((s)->flags&(f))
#define ps_dbg(...) ((void)0)
#define spin_lock(p) ((void)(p))
#define spin_unlock(p) ((void)(p))
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
typedef enum {TX_CONTINUE,TX_DROP,TX_QUEUED} ieee80211_tx_result;
enum ieee80211_frame_release_type {POLL,UAPSD};
struct ieee80211_tx_info {unsigned flags;struct {unsigned jiffies;unsigned flags;void *vif;}control;};
/* Production jiffies macro must not rewrite the member name. */
#undef jiffies
static const unsigned jiffies=10;
struct sk_buff {struct sk_buff *next;unsigned truesize,id,ac;bool expired;_Alignas(2) unsigned char data[8];struct ieee80211_tx_info info;};
struct sk_buff_head {struct sk_buff *head,*tail;unsigned qlen;int lock;};
struct ieee80211_hdr {unsigned short frame_control;};
struct ieee80211_local {atomic_t ps_buffered_bytes;int total_ps_buffered,sta_cleanup,hw;};
struct ieee80211_sub_if_data {struct ieee80211_local *local;int vif;};
struct sta_info {struct ieee80211_sub_if_data*sdata;atomic_t ps_buffered_bytes;int flags,ps_lock;unsigned long driver_buffered_tids,txq_buffered_tids;struct sk_buff_head ps_tx_buf[4],tx_filtered[4];};
struct ieee80211_tx_data {struct sta_info*sta;struct ieee80211_local*local;struct sk_buff*skb;struct ieee80211_sub_if_data*sdata;};
#define IEEE80211_SKB_CB(s) (&(s)->info)
#define skb_get_queue_mapping(s) ((s)->ac)
#define skb_queue_walk(q,s) for(s=(q)->head;s;s=s->next)
static unsigned freed,tim;
static unsigned skb_queue_len(struct sk_buff_head*q){return q->qlen;}
static bool skb_queue_empty(struct sk_buff_head*q){return !q->qlen;}
static struct sk_buff *skb_peek(struct sk_buff_head*q){return q->head;}
static void skb_queue_tail(struct sk_buff_head*q,struct sk_buff*s){s->next=0;if(q->tail)q->tail->next=s;else q->head=s;q->tail=s;q->qlen++;}
#define __skb_queue_tail skb_queue_tail
static struct sk_buff *skb_dequeue(struct sk_buff_head*q){struct sk_buff*s=q->head;if(s){q->head=s->next;if(!q->head)q->tail=0;q->qlen--;s->next=0;}return s;}
#define __skb_dequeue skb_dequeue
static void ieee80211_free_txskb(void*h,struct sk_buff*s){(void)h;assert(s);freed++;free(s);}
static bool ieee80211_is_mgmt(unsigned fc){return fc==0;}
static bool ieee80211_is_bufferable_mmpdu(struct sk_buff*s){return s->data[2];}
static bool timer_pending(int*t){return *t;}
static void mod_timer(int*t,unsigned when){(void)when;*t=1;}
static void sta_info_recalc_tim(struct sta_info*s){tim=0;for(int i=0;i<4;i++)tim+=s->ps_tx_buf[i].qlen+s->tx_filtered[i].qlen;}
static bool sta_info_buffer_expired(struct sta_info*s,struct sk_buff*b){(void)s;return b&&b->expired;}
static unsigned ieee80211_ac_to_qos_mask[]={1,2,4,8};
static unsigned long ieee80211_tids_for_ac(int ac){return 3UL<<(2*ac);}
'''+h+func((d/'tx.c').read_text(),'ieee80211_tx_h_unicast_ps_buf')+func((d/'sta_info.c').read_text(),'ieee80211_sta_ps_get_frames')+func((d/'sta_info.c').read_text(),'sta_info_cleanup_expire_buffered_ac')+r'''
static struct sk_buff *packet(unsigned id,unsigned size,unsigned ac){struct sk_buff*s=calloc(1,sizeof(*s));assert(s);s->truesize=size;s->id=id;s->ac=ac;s->data[0]=8;return s;}
static int enqueue(struct ieee80211_local*l,struct sta_info*s,unsigned id,unsigned size,unsigned ac){struct sk_buff*b=packet(id,size,ac);struct ieee80211_tx_data t={s,l,b,s->sdata};int ret=ieee80211_tx_h_unicast_ps_buf(&t);if(ret!=TX_QUEUED)free(b);return ret;}
static void purge(struct ieee80211_local*l,struct sta_info*s){for(int a=0;a<4;a++){ieee80211_ps_uncharge_queue(l,s,&s->ps_tx_buf[a]);l->total_ps_buffered-=s->ps_tx_buf[a].qlen;struct sk_buff*b;while((b=skb_dequeue(&s->ps_tx_buf[a])))free(b);}}
int main(void){struct ieee80211_local l={};struct ieee80211_sub_if_data sd={.local=&l};struct sta_info s={.sdata=&sd,.flags=WLAN_STA_PS_STA};
 assert(enqueue(&l,&s,0,IEEE80211_PS_STA_BYTES+1,0)==TX_DROP&&!l.ps_buffered_bytes);
 for(unsigned i=0;i<1024;i++)assert(enqueue(&l,&s,i,8192,0)==TX_QUEUED);
 assert(s.ps_buffered_bytes==IEEE80211_PS_STA_BYTES&&l.total_ps_buffered==1024&&tim==1024);
 assert(enqueue(&l,&s,1024,8192,0)==TX_QUEUED);assert(freed==1&&s.ps_tx_buf[0].head->id==1);
 assert(enqueue(&l,&s,2000,IEEE80211_PS_STA_BYTES+1,0)==TX_DROP&&freed==1);
 struct sk_buff_head out={};unsigned long tids=0;
 ieee80211_sta_ps_get_frames(&s,1,0,POLL,&out,&tids);struct sk_buff*b=skb_dequeue(&out);assert(b->id==1&&!tids);free(b);
 assert(l.ps_buffered_bytes==IEEE80211_PS_STA_BYTES-8192&&s.ps_tx_buf[0].qlen==1023);
 ieee80211_sta_ps_get_frames(&s,6,0,UAPSD,&out,&tids);for(unsigned i=2;i<8;i++){b=skb_dequeue(&out);assert(b->id==i);free(b);}
 assert(l.total_ps_buffered==1017);
 s.ps_tx_buf[0].head->expired=true;assert(sta_info_cleanup_expire_buffered_ac(&l,&s,0));assert(s.ps_tx_buf[0].head->id==9&&tim==1016);
 purge(&l,&s);assert(!l.ps_buffered_bytes&&!s.ps_buffered_bytes&&!l.total_ps_buffered);
 /* A voice frame can reclaim background memory; background cannot evict voice. */
 for(unsigned i=0;i<1024;i++)assert(enqueue(&l,&s,i,8192,3)==TX_QUEUED);
 assert(enqueue(&l,&s,2000,8192,0)==TX_QUEUED);
 assert(s.ps_tx_buf[3].qlen==1023&&s.ps_tx_buf[0].qlen==1);
 purge(&l,&s);
 for(unsigned i=0;i<1024;i++)assert(enqueue(&l,&s,i,8192,0)==TX_QUEUED);
 assert(enqueue(&l,&s,2000,8192,3)==TX_DROP&&s.ps_tx_buf[0].qlen==1024);
 purge(&l,&s);
 s.flags=0;assert(enqueue(&l,&s,1,8192,0)==TX_CONTINUE&&!l.ps_buffered_bytes);
 s.flags=WLAN_STA_PS_STA;for(int i=0;i<4;i++)assert(enqueue(&l,&s,i,4096,i)==TX_QUEUED);
 s.driver_buffered_tids=1;tids=0;ieee80211_sta_ps_get_frames(&s,1,0,POLL,&out,&tids);assert(tids==1&&skb_queue_empty(&out)&&l.total_ps_buffered==4);s.driver_buffered_tids=0;
 tids=0;ieee80211_sta_ps_get_frames(&s,2,1,UAPSD,&out,&tids);assert(s.ps_tx_buf[0].qlen==1&&out.qlen==2);while((b=skb_dequeue(&out)))free(b);
 purge(&l,&s);assert(!l.ps_buffered_bytes&&!s.ps_buffered_bytes&&!l.total_ps_buffered);
 puts("actual PS enqueue/poll/UAPSD/expiry: limits, eviction order, TIM, driver priority and uncharge PASS");}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.c').write_text(code);subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-unused-function','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(p/'test.c'),'-o',str(p/'test')],check=True);subprocess.run([str(p/'test')],check=True)
