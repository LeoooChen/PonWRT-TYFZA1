#!/usr/bin/env python3
"""Run production uplink synchronization against stalled and racing PE records."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


root = Path(__file__).resolve().parents[1]
relative = Path('port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rx-offload-state.h')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision')
args = parser.parse_args()
source = (subprocess.check_output(['git', 'show', f'{args.revision}:{relative}'], cwd=root, text=True)
          if args.revision else (root / relative).read_text())


def function(name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
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
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define READ_ONCE(p) (p)
#define min_t(t,a,b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
struct ieee80211_key_conf {u16 next;u8 pn[6];};
struct ieee80211_sta {unsigned macid;};
struct rtl_ax_shortcut_seen {u32 frames,bytes,msdus;};
struct rtl_ax_rx {
    struct {u8 *memory;} pe;u8 *uplink_states,*rpq_memory;void *hw;
    struct ieee80211_key_conf *keys[128];int uplink_lock,irq_lock,work;
    unsigned uplink_wifi_id;u16 uplink_pending_tids[128];
    struct rtl_ax_shortcut_seen uplink_seen[128][16];u32 uplink_forwarded_seen;
    bool running,rpq_transfer;
};
static struct rtl_ax_rx *active;
static struct ieee80211_sta stations[128];
static unsigned bh,rcu,queued,guard_reads,snapshots,syncs[128][16],accounts[128][16];
static u32 counted_frames[128][16],counted_bytes[128][16],counted_msdus[128][16];
static u8 *racing_state;
static bool race,snapshot_blocked;
static bool work_pending;
static unsigned work_delay;
static void *system_wq;
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void local_bh_disable(void){assert(!bh);bh=1;}
static void local_bh_enable(void){assert(bh&&!rcu);bh=0;}
static void rcu_read_lock(void){assert(bh&&!rcu);rcu=1;}
static void rcu_read_unlock(void){assert(bh&&rcu);rcu=0;}
#define spin_lock_irqsave(p,f) do{assert(!*(p));*(p)=1;(f)=0;}while(0)
#define spin_unlock_irqrestore(p,f) do{assert(*(p));*(p)=0;(void)(f);}while(0)
#define dma_rmb() ((void)0)
#define dma_wmb() ((void)0)
static void put_unaligned_le32(u32 value,void *p){memcpy(p,&value,4);}
static u32 raw(const void *p){u32 value;memcpy(&value,p,4);return value;}
static u32 get_unaligned_le32(const void *p){
    if(p==racing_state+24){
        /* A second attempt inside the read section would exceed the budget. */
        assert(++guard_reads<=2);
        if(race&&guard_reads==2){
            put_unaligned_le32(4,racing_state+24);
            put_unaligned_le32(BIT(31)|4095,racing_state);
            put_unaligned_le32(0x33445577,racing_state+4);
            put_unaligned_le32(raw(racing_state+12)+1,racing_state+12);
            put_unaligned_le32(raw(racing_state+16)+100,racing_state+16);
            put_unaligned_le32(raw(racing_state+20)+2,racing_state+20);
            put_unaligned_le32(raw(active->pe.memory+0x2180)+1,active->pe.memory+0x2180);
        }
    }
    return raw(p);
}
static void mod_delayed_work(void *queue,int *work,unsigned delay){
    (void)queue;assert(work==&active->work&&delay>0);
    assert(!bh&&!rcu&&!active->uplink_lock&&active->irq_lock);
    assert(active->running&&!active->rpq_transfer);queued++;
    work_pending=true;work_delay=delay;
}
static void queue_delayed_work(void *queue,int *work,unsigned delay){
    (void)queue;assert(work==&active->work&&delay>0);
    assert(!bh&&!rcu&&!active->uplink_lock&&active->irq_lock);
    assert(active->running&&!active->rpq_transfer);
    if(work_pending)return;
    work_pending=true;work_delay=delay;queued++;
}
static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(void *hw,const u8 *address,void *unused){
    (void)hw;(void)unused;assert(bh&&rcu&&active->uplink_lock);
    return &stations[*address];
}
static bool ieee80211_rx_shortcut_snapshot(struct ieee80211_sta *sta,
        struct ieee80211_key_conf *key,unsigned tid,u16 *next,u8 pn[6]){
    (void)tid;assert(bh&&rcu&&active->uplink_lock&&key==active->keys[sta->macid]);
    if(snapshot_blocked)return false;
    *next=key->next;memcpy(pn,key->pn,6);snapshots++;return true;
}
static void ieee80211_rx_shortcut_sync(struct ieee80211_sta *sta,
        struct ieee80211_key_conf *key,unsigned tid,u16 next,const u8 pn[6],u16 received){
    assert(bh&&rcu&&active->uplink_lock&&key==active->keys[sta->macid]);
    u8 *state=active->uplink_states+(sta->macid*16+tid)*32;
    assert(!(raw(state+24)&1)&&received==min_t(u32,raw(state+12)-active->uplink_seen[sta->macid][tid].frames,2048));
    assert(next==(raw(state)&4095));
    assert(pn[0]==(u8)(raw(state+8)>>8)&&pn[1]==(u8)raw(state+8));
    assert(pn[2]==(u8)(raw(state+4)>>24)&&pn[3]==(u8)(raw(state+4)>>16));
    assert(pn[4]==(u8)(raw(state+4)>>8)&&pn[5]==(u8)raw(state+4));
    assert(memcmp(pn,key->pn,6)>=0);memcpy(key->pn,pn,6);key->next=next;
    syncs[sta->macid][tid]++;
}
static void ieee80211_rx_shortcut_account(struct ieee80211_sta *sta,unsigned tid,
        u32 frames,u32 msdus,u32 bytes){
    assert(bh&&rcu&&active->uplink_lock);accounts[sta->macid][tid]++;
    counted_frames[sta->macid][tid]+=frames;counted_msdus[sta->macid][tid]+=msdus;counted_bytes[sta->macid][tid]+=bytes;
}
'''

case = r'''
static void record(u8 *state,u32 frames,u32 bytes,u32 msdus){
    put_unaligned_le32(BIT(31)|4094,state);put_unaligned_le32(0x33445566,state+4);
    put_unaligned_le32(0x1122,state+8);put_unaligned_le32(frames,state+12);
    put_unaligned_le32(bytes,state+16);put_unaligned_le32(msdus,state+20);
    put_unaligned_le32(2,state+24);put_unaligned_le32(64,state+28);
}
int main(void){
    (void)system_wq;(void)mod_delayed_work;(void)queue_delayed_work;
    static u8 memory[0x2200],rpq[0xe800],states[0x10200];
    struct ieee80211_key_conf key={0},other={0};
    struct rtl_ax_rx rx={.pe={memory},.uplink_states=states,.rpq_memory=rpq,
        .uplink_wifi_id=1,.running=true,.uplink_forwarded_seen=9};
    active=&rx;rx.keys[7]=&key;rx.keys[8]=&other;stations[7].macid=7;stations[8].macid=8;
    rpq[0xe400+7*8]=7;rpq[0xe400+8*8]=8;
    u8 *state=states+(7*16+3)*32,*second=states+(8*16+4)*32;
    racing_state=state;record(state,3,300,6);record(second,2,200,2);
    put_unaligned_le32(10,memory+0x2180);put_unaligned_le32(1,state+24);
    put_unaligned_le32(BIT(31),states+0x10000+7*4);rx.uplink_pending_tids[7]=BIT(3);
    u8 before[32];memcpy(before,state,32);
    rtl_ax_rx_uplink_sync(&rx);
    assert(guard_reads==1&&queued==1&&!bh&&!rcu&&!rx.uplink_lock&&!rx.irq_lock);
    assert(!syncs[7][3]&&!accounts[7][3]&&!snapshots&&!memcmp(before,state,32));
    assert(rx.uplink_pending_tids[7]==BIT(3)&&rx.uplink_forwarded_seen==9);
    assert(syncs[8][4]==1&&accounts[8][4]==1&&counted_frames[8][4]==2);
    /* No new global publication: the failed TID still has to be retried. */
    put_unaligned_le32(2,state+24);guard_reads=0;
    rtl_ax_rx_uplink_sync(&rx);
    assert(guard_reads==2&&queued==1&&rx.uplink_forwarded_seen==10);
    assert(syncs[7][3]==1&&accounts[7][3]==1&&snapshots==1&&!rx.uplink_pending_tids[7]);
    assert(counted_frames[7][3]==3&&counted_bytes[7][3]==300&&counted_msdus[7][3]==6);
    assert(raw(state)==(BIT(31)|4094)&&raw(state+4)==0x33445566&&raw(state+8)==0x1122&&raw(state+28)==64);
    assert(syncs[8][4]==1&&accounts[8][4]==1);
    guard_reads=0;rtl_ax_rx_uplink_sync(&rx);assert(!guard_reads&&queued==1&&syncs[7][3]==1);
    /* PE changes its record and global marker during the snapshot. */
    work_pending=false;
    put_unaligned_le32(11,memory+0x2180);record(state,4,400,8);
    rx.uplink_pending_tids[7]=BIT(3);race=true;guard_reads=0;
    rtl_ax_rx_uplink_sync(&rx);
    assert(guard_reads==2&&queued==2&&rx.uplink_forwarded_seen==10);
    assert(syncs[7][3]==1&&accounts[7][3]==1&&snapshots==1&&rx.uplink_pending_tids[7]==BIT(3));
    race=false;guard_reads=0;rtl_ax_rx_uplink_sync(&rx);
    assert(syncs[7][3]==2&&accounts[7][3]==2&&snapshots==2&&!rx.uplink_pending_tids[7]);
    assert(counted_frames[7][3]==5&&counted_bytes[7][3]==500&&counted_msdus[7][3]==10);
    assert(rx.uplink_forwarded_seen==12&&key.next==4095&&key.pn[5]==0x77);
    /* Wrapping per-TID counters retain their unsigned delta semantics. */
    rx.uplink_seen[7][3]=(struct rtl_ax_shortcut_seen){UINT32_MAX-2,UINT32_MAX-99,UINT32_MAX-1};
    record(state,1,200,2);put_unaligned_le32(13,memory+0x2180);
    memset(key.pn,0,6);guard_reads=0;rtl_ax_rx_uplink_sync(&rx);
    assert(counted_frames[7][3]==9&&counted_bytes[7][3]==800&&counted_msdus[7][3]==14);
    /* Retiring or stopped RX must not acquire a new retry work item. */
    put_unaligned_le32(14,memory+0x2180);record(state,2,300,3);put_unaligned_le32(1,state+24);
    rx.rpq_transfer=true;guard_reads=0;rtl_ax_rx_uplink_sync(&rx);assert(queued==2);
    rx.rpq_transfer=false;rx.running=false;guard_reads=0;rtl_ax_rx_uplink_sync(&rx);assert(queued==2);
    assert(!bh&&!rcu&&!rx.uplink_lock&&!rx.irq_lock);
    /* ARM's reorder timer may finish without another DMA packet. A failed
     * seed must schedule its own retry even with no new PE publication. */
    work_pending=false;rx.running=true;rx.uplink_forwarded_seen=14;
    rx.uplink_pending_tids[7]=BIT(3);snapshot_blocked=true;guard_reads=0;
    put_unaligned_le32(0,state);
    rtl_ax_rx_uplink_sync(&rx);
    assert(queued==3 && rx.uplink_pending_tids[7]==BIT(3) && !raw(state));
    snapshot_blocked=false;guard_reads=0;
    rtl_ax_rx_uplink_sync(&rx);
    assert(queued==3 && !rx.uplink_pending_tids[7] && (raw(state)&BIT(31)));
    /* An IRQ queued immediate RX while the current worker was running.
     * A failed reorder snapshot must not move that work ten ms later. */
    rx.uplink_pending_tids[7]=BIT(3);snapshot_blocked=true;
    work_pending=true;work_delay=0;guard_reads=0;
    rtl_ax_rx_uplink_sync(&rx);
    assert(queued==3 && work_pending && work_delay==0);
    /* Repeated failed snapshots do not restart an existing retry timer. */
    work_delay=1;guard_reads=0;rtl_ax_rx_uplink_sync(&rx);
    assert(queued==3 && work_delay==1);
    work_pending=false;guard_reads=0;rtl_ax_rx_uplink_sync(&rx);
    assert(queued==4 && work_pending && work_delay==1);
    puts("Uplink sync: stalled/torn records, deferred retry, independent TIDs, PN/SN, BA window, counter wrap and shutdown passed");
}
'''

with tempfile.TemporaryDirectory(prefix='rx-uplink-sync-', dir=root / 'artifacts') as directory:
    directory = Path(directory)
    code = directory / 'case.c'
    code.write_text(common + function('rtl_ax_rx_uplink_publish') + function('rtl_ax_rx_uplink_sync') + case)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(code), '-o', str(directory / 'case')], check=True)
    subprocess.run([str(directory / 'case')], check=True, timeout=10)
