#!/usr/bin/env python3
"""Exercise the actual RPQ handler with mocked mac80211/BH ownership."""
from pathlib import Path
import argparse, subprocess, tempfile
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path)
args=parser.parse_args()
path=args.source or root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c'
source=path.read_text()
def function(s,name):
    start=s.index('int '+name+'('); at=s.index('{',start)+1; depth=1
    while depth:
        depth+=(s[at]=='{')-(s[at]=='}'); at+=1
    return s[start:at]
after=function(source,'rtl_ax_tx_report')
assert after.count('local_bh_disable();') == 4
assert after.count('local_bh_enable();') == 4
before=after.replace('\n\tlocal_bh_disable();','').replace('\n\tlocal_bh_enable();','')
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef uint8_t u8; typedef uint16_t __le16; typedef uint32_t u32; typedef uint64_t u64;
#define BIT(x) (1U<<(x))
#define RTL_AX_TX_ENTRIES 256
#define RTL_AX_TX_PAGES 384
#define RTL_AX_TX_RINGS 6
#define RTL_AX_NI_PS_WP_GENERATION 0x15000
#define PE_PROBE_MAILBOX 0x2000
#define NL80211_BAND_5GHZ 1
#define IEEE80211_TX_CTL_HW_80211_ENCAP BIT(0)
#define IEEE80211_TX_CTL_NO_ACK BIT(1)
#define IEEE80211_TX_STAT_NOACK_TRANSMITTED BIT(2)
#define IEEE80211_TX_STAT_ACK BIT(3)
#define IEEE80211_TX_CTL_AMPDU BIT(4)
#define IEEE80211_TX_STAT_AMPDU BIT(5)
#define IEEE80211_TX_CTL_RATE_CTRL_PROBE BIT(6)
#define IEEE80211_TX_RC_MCS BIT(7)
#define IEEE80211_TX_RC_SHORT_GI BIT(8)
#define READ_ONCE(x) (x)
struct rate {int idx,flags,count;};
struct ieee80211_tx_info {unsigned flags,band; struct {struct rate rates[4];unsigned ampdu_len,ampdu_ack_len;}status;};
struct sk_buff {u8 data[64];struct ieee80211_tx_info cb;};
#define IEEE80211_SKB_CB(s) (&(s)->cb)
struct ieee80211_hdr {__le16 fc,duration;u8 addresses[18];__le16 seq_ctrl;};
struct ieee80211_sta {int unused;};struct ieee80211_hw {int unused;};
struct ieee80211_tx_status {struct sk_buff *skb;struct ieee80211_tx_info *info;struct ieee80211_sta *sta;};
struct rtl_ax_tx_slot {struct sk_buff *skb;unsigned ring,macid;bool pe_sequence,firmware_rate;};
struct rtl_ax_rx_packet {unsigned bytes;u8 *payload;bool pe_completed;};
/* The EWMA arithmetic is covered by test_radio_mesh_metric; record each
 * update here to compare RPQ dispatch ordering and ownership. */
struct ewma_rtl_ax_success {unsigned total,calls;};
static void ewma_rtl_ax_success_add(struct ewma_rtl_ax_success *p,unsigned value){p->total+=value;p->calls++;}
struct rtl_ax_tx {struct ewma_rtl_ax_success peer_success[128];int lock,key_flush_wait;struct {int ps_work;}ni;struct {void *memory;}pe;u8 *headers;
 bool pe_active,running,queues_paused,queues_stopped,peer_asleep[128],pending[256];
 struct ieee80211_hw *hw;struct rtl_ax_tx_slot slots[256];
 unsigned occupied,last_report,unmatched,completed,completed_ring[6],status_count[8],polluted,queue_wakes;
 unsigned ni_reports,ni_failed,ni_first_report_ns,ampdu_completed;};
static unsigned bh,rcu,locks,wakes,unmaps,returns,raws,pe_done,ps_done,scheduled,waiters,lookups,dispatches;
static unsigned first_dispatch_completed,expected,batch;
static struct rtl_ax_tx *active;static bool runnable;static struct ieee80211_sta sta;
static void local_bh_disable(void){bh++;}
static void local_bh_enable(void){assert(bh);if(!--bh&&runnable){
 assert(!locks&&!rcu);runnable=false;dispatches++;if(dispatches==1)first_dispatch_completed=active->completed;
 if(batch)assert(active->completed==expected);}}
static void rcu_read_lock(void){assert(bh);rcu++;}
static void rcu_read_unlock(void){assert(rcu);rcu--;}
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0,locks++)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f),assert(locks),locks--)
#define test_bit(id,p) ((p)[id])
#define __clear_bit(id,p) ((p)[id]=false)
static u32 get_unaligned_le32(const void*p){u32 v;memcpy(&v,p,4);return v;}
static __le16 get_unaligned(const __le16*p){__le16 v;memcpy(&v,p,2);return v;}
#define rtl_ax_ap_read32(p) get_unaligned_le32(p)
static void schedule_work(void*p){(void)p;scheduled++;}
static void wake_up_all(void*p){(void)p;waiters++;}
static void ni_route_sleep(struct rtl_ax_tx*t,unsigned id){assert(t==active&&id<128);scheduled++;}
static void ni_ps_complete(struct rtl_ax_tx*t,struct ieee80211_sta*s,unsigned id,u32 generation){assert(t==active&&s&&id<128&&generation==77&&bh&&rcu);ps_done++;}
static void rtl_ax_tx_pe_complete(struct rtl_ax_tx*t,unsigned id,unsigned status){assert(t==active&&id>=256&&id<384&&status<8);pe_done++;}
static u64 ktime_get_ns(void){return 1;}
static struct ieee80211_sta*ieee80211_find_sta_by_ifaddr(struct ieee80211_hw*h,const u8*a,const void*b){assert(h&&a&&!b&&bh&&rcu);lookups++;return &sta;}
static void ieee80211_wake_queues(struct ieee80211_hw*h){assert(h&&locks);wakes++;runnable=true;}
static void rtl_ax_tx_unmap(struct rtl_ax_tx*t,struct rtl_ax_tx_slot*s){assert(t==active&&!locks);if(batch)assert(bh==1);unsigned id=s->skb->data[0];assert(!t->pending[id]&&!t->slots[id].skb);unmaps++;}
static void ieee80211_tx_info_clear_status(struct ieee80211_tx_info*i){memset(&i->status,0,sizeof(i->status));}
static void ieee80211_tx_status_ext(struct ieee80211_hw*h,struct ieee80211_tx_status*s){assert(h&&s->info&&s->sta&&bh&&rcu);if(s->skb){returns++;runnable=true;}}
static void ieee80211_tx_status_skb(struct ieee80211_hw*h,struct sk_buff*s){assert(h&&s&&bh&&!locks);raws++;returns++;runnable=true;}
static void ieee80211_tx_status_ni(struct ieee80211_hw*h,struct sk_buff*s){assert(h&&s);local_bh_disable();ieee80211_tx_status_skb(h,s);local_bh_enable();}
'''
code+=before.replace('int rtl_ax_tx_report(', 'static int old_report(')+'\n'
code+=after.replace('int rtl_ax_tx_report(', 'static int new_report(')+'\n'
code+=r'''
struct result {struct ewma_rtl_ax_success peer_success[128];unsigned completed,occupied,unmatched,polluted,queue_wakes,ni_reports,ni_failed,ampdu;
 unsigned completed_ring[6],status_count[8],flags[256],seq[256];
 unsigned wakes,unmaps,returns,raws,pe_done,ps_done,scheduled,waiters,lookups;};
static struct result run(bool batching,unsigned count,unsigned mode){
 static u8 mem[0x20000],headers[256*128];static struct sk_buff skbs[256];
 static u32 reports[264];struct ieee80211_hw hw={};struct rtl_ax_tx t={.pe_active=true,.running=true,.queues_stopped=true,.hw=&hw,.pe={mem},.headers=headers};
 memset(mem,0,sizeof(mem));memset(headers,0,sizeof(headers));memset(skbs,0,sizeof(skbs));
 bh=rcu=locks=wakes=unmaps=returns=raws=pe_done=ps_done=scheduled=waiters=lookups=dispatches=0;
 first_dispatch_completed=0;active=&t;runnable=false;batch=batching;expected=count;
 u32 enabled=mode&1;memcpy(mem+PE_PROBE_MAILBOX+96,&enabled,4);
 t.occupied=count;t.queues_paused=!!(mode&2);t.running=!(mode&4);
 for(unsigned n=0;n<count;n++){
  unsigned ring=n%6,qsel=ring<4?ring:ring==4?18:17,status=(mode&8)?n%8:0;
  reports[n]=1|qsel<<8|status<<13|n<<16|((mode&16)&&n%3==0?BIT(31):0);
  skbs[n].data[0]=n;
  skbs[n].cb.flags=((mode&32)&&n%2?0:IEEE80211_TX_CTL_HW_80211_ENCAP)|((n%3)?IEEE80211_TX_CTL_AMPDU:0)|((n%5)?0:IEEE80211_TX_CTL_NO_ACK);
  t.slots[n]=(struct rtl_ax_tx_slot){.skb=&skbs[n],.ring=ring,.macid=n%128,.pe_sequence=(n%2)==1};t.pending[n]=true;
  t.peer_asleep[n%128]=!!(n%3);__le16 seq=0xabc0+n;memcpy(headers+n*128+22,&seq,2);
  u32 ra=BIT(17);memcpy(mem+0xe200+(n%128)*4,&ra,4);
 }
 reports[count]=1|400<<16;reports[count+1]=1|7<<8|254<<16;
 reports[count+2]=count?reports[0]:1; // duplicate or empty pending ID
 unsigned member=127,meta=(3<<8)|BIT(16),generation=77,rate=BIT(20)|0x80;
 memcpy(mem+0xc400+member*8+4,&meta,4);memcpy(mem+RTL_AX_NI_PS_WP_GENERATION+member*4,&generation,4);memcpy(mem+0xe000+member*4,&rate,4);
 reports[count+3]=1|383<<16;
 struct rtl_ax_rx_packet packet={.bytes=(count+4)*4+(mode%4),.payload=(void*)reports};
 assert(!(batching?new_report(&t,&hw,&packet):old_report(&t,&hw,&packet)));
 assert(!bh&&!rcu&&!locks&&t.completed==count&&t.occupied==0&&returns==count&&unmaps==count&&pe_done==1&&ps_done==1);
 packet.pe_completed=true;packet.bytes=4;packet.payload=(void*)&reports[count+3];
 assert(!(batching?new_report(&t,&hw,&packet):old_report(&t,&hw,&packet)));
 packet.bytes=0;assert(!(batching?new_report(&t,&hw,&packet):old_report(&t,&hw,&packet)));
 struct result r={.completed=t.completed,.occupied=t.occupied,.unmatched=t.unmatched,.polluted=t.polluted,.queue_wakes=t.queue_wakes,.ni_reports=t.ni_reports,.ni_failed=t.ni_failed,.ampdu=t.ampdu_completed,
 .wakes=wakes,.unmaps=unmaps,.returns=returns,.raws=raws,.pe_done=pe_done,.ps_done=ps_done,.scheduled=scheduled,.waiters=waiters,.lookups=lookups};
 memcpy(r.peer_success,t.peer_success,sizeof(r.peer_success));
 memcpy(r.completed_ring,t.completed_ring,sizeof(r.completed_ring));memcpy(r.status_count,t.status_count,sizeof(r.status_count));
 for(unsigned n=0;n<count;n++){r.flags[n]=skbs[n].cb.flags;r.seq[n]=((struct ieee80211_hdr*)skbs[n].data)->seq_ctrl;}
 return r;
}
int main(void){unsigned cases=0;for(unsigned n=0;n<=256;n++)for(unsigned mode=0;mode<64;mode++){
 struct result a=run(false,n,mode),b=run(true,n,mode);assert(!memcmp(&a,&b,sizeof(a)));cases++;}
 printf("Actual RPQ function: %u before/after vectors; ownership/status/NI/PS identical; batch dispatch only after full report PASS\n",cases);}
'''
with tempfile.TemporaryDirectory(prefix='rpq-batch-') as tmp:
    work=Path(tmp)
    (work/'test.c').write_text(code)
    subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(work/'test.c'),'-o',str(work/'test')],check=True)
    subprocess.run([str(work/'test')],check=True)
