#!/usr/bin/env python3
"""Exercise actual RX synchronization: unchanged, published, pending and next poll."""
from pathlib import Path
import subprocess, tempfile, sys
ROOT = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rx.c'
text = source.read_text()
start = text.index('static void rtl_ax_rx_uplink_sync(')
end = text.index('\nvoid rtl_ax_rx_uplink_retire(', start)
body = text[start:end]
model = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
typedef uint8_t u8; typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
struct ieee80211_key_conf { unsigned id; } keys[2]={{1},{2}};
struct ieee80211_sta { unsigned id; } stations[2]={{1},{2}};
struct rtl_ax_shortcut_seen { u32 frames,bytes,msdus; };
struct rtl_ax_rx {
 void *rpq_memory,*uplink_states,*hw; unsigned uplink_wifi_id; u32 uplink_forwarded_seen;
 int uplink_lock; struct ieee80211_key_conf *keys[128];
 struct rtl_ax_shortcut_seen uplink_seen[128][16];
 unsigned uplink_pending_tids[128];
};
static unsigned reads,syncs,accounts,publishes,last_tid,last_next,last_count;
static u32 account_frames,account_msdus,account_bytes;
static u8 last_pn[6];
static u32 get_unaligned_le32(const void *p) { u32 v;memcpy(&v,p,4);reads++;return v; }
static void mutex_lock(int *p){(void)p;}
static void mutex_unlock(int *p){(void)p;}
static void local_bh_disable(void){}
static void local_bh_enable(void){}
static void rcu_read_lock(void){}
static void rcu_read_unlock(void){}
static void dma_rmb(void){}
static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(void *hw,u8 *address,void *unused)
{ (void)hw;(void)unused;assert(address[0]==1 || address[0]==2);return &stations[address[0]-1]; }
static void ieee80211_rx_shortcut_sync(struct ieee80211_sta *sta,struct ieee80211_key_conf *key,
 unsigned tid,unsigned next,const u8 pn[6],unsigned count)
{ assert(sta->id==key->id);syncs++;last_tid=tid;last_next=next;last_count=count;memcpy(last_pn,pn,6); }
static void ieee80211_rx_shortcut_account(struct ieee80211_sta *sta,unsigned tid,u32 frames,u32 msdus,u32 bytes)
{ (void)sta;(void)tid;accounts++;account_frames=frames;account_msdus=msdus;account_bytes=bytes; }
static bool rtl_ax_rx_uplink_publish(struct rtl_ax_rx *rx,struct ieee80211_sta *sta,
 struct ieee80211_key_conf *key,unsigned id,unsigned tid)
{ assert(sta->id==id && key->id==id);publishes++;rx->uplink_pending_tids[id]&=~BIT(tid);return true; }
'''
main = r'''
int main(void) {
 struct rtl_ax_rx rx={.uplink_wifi_id=1};
 rx.rpq_memory=calloc(1,131072);rx.uplink_states=calloc(1,131072);
 assert(rx.rpq_memory && rx.uplink_states);
 for(unsigned id=1;id<=2;id++) {
  rx.keys[id]=&keys[id-1];((u8 *)rx.rpq_memory)[0xe400+id*8]=id;
  *(u32 *)((u8 *)rx.uplink_states+0x10000+id*4)=BIT(31);
 }
 rtl_ax_rx_uplink_sync(&rx);
 unsigned inactive_reads=reads;
 assert(!syncs && !accounts && !publishes);
 /* One radio count suffices when no TID has published new frames. */
 assert(inactive_reads==1);
 u32 *s=(void *)((u8 *)rx.uplink_states+(1*16+3)*32);
 s[0]=BIT(31)|123;s[1]=0x3456789a;s[2]=0x1234;s[3]=7;s[4]=7000;s[5]=9;s[6]=2;
 rx.uplink_seen[1][3]=(struct rtl_ax_shortcut_seen){5,5000,6};
 *(u32 *)((u8 *)rx.rpq_memory+0x2180)=9;
 rtl_ax_rx_uplink_sync(&rx);
 assert(syncs==1 && accounts==1 && last_tid==3 && last_next==123 && last_count==2);
 assert(account_frames==2 && account_msdus==3 && account_bytes==2000);
 assert(!memcmp(last_pn,"\x12\x34\x34\x56\x78\x9a",6));
 assert(rx.uplink_seen[1][3].frames==7 && rx.uplink_seen[1][3].bytes==7000);
 rtl_ax_rx_uplink_sync(&rx);assert(syncs==1 && accounts==1);
 /* Timeout-driven reorder recovery runs even with no new PE publications. */
 rx.uplink_pending_tids[2]=BIT(4);rtl_ax_rx_uplink_sync(&rx);
 assert(publishes==1 && !rx.uplink_pending_tids[2] && syncs==1);
 /* A publication after an idle poll is consumed exactly once on the next. */
 s[0]=BIT(31)|124;s[1]++;s[3]++;s[4]+=100;s[5]++;s[6]+=2;
 *(u32 *)((u8 *)rx.rpq_memory+0x2180)=10;
 rtl_ax_rx_uplink_sync(&rx);rtl_ax_rx_uplink_sync(&rx);
 assert(syncs==2 && accounts==2 && last_next==124 && last_count==1);
 assert(account_frames==1 && account_msdus==1 && account_bytes==100);
 printf("{\"inactive_state_reads\":%u,\"publication_syncs\":%u,\"pending_resume\":%u}\n",inactive_reads,syncs,publishes);
 free(rx.rpq_memory);free(rx.uplink_states);return 0;
}
'''
with tempfile.TemporaryDirectory() as directory:
    p=Path(directory);(p/'test.c').write_text(model+body+main)
    subprocess.run(['cc','-O2','-Wall','-Wextra',str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
