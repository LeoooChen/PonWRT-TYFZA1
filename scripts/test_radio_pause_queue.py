#!/usr/bin/env python3
"""Execute native pause/open and dequeue with packets queued across a pause."""
from pathlib import Path
import argparse
import json
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--before', action='store_true')
args = parser.parse_args()
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
tx = (driver / 'tx.c').read_text()
if args.before:
    tx = subprocess.check_output(['git', 'show', 'f07ed6f:' + str((driver / 'tx.c').relative_to(root))], cwd=root, text=True)
monitor = (driver / 'monitor.h').read_text()


def function(text, name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', text, re.M).start()
    at = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at] + '\n'


def block(text, prefix):
    start = text.index(prefix)
    at = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at]


common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#define RTL_AX_TX_ENTRIES 256
#define RTL_AX_TX_STOP_MASK 0x303
#define PE_PROBE_MAILBOX 0x2000
#define READ_ONCE(p) (p)
#define WRITE_ONCE(p,v) ((p)=(v))
#define xchg(p,v) __atomic_exchange_n(p,v,__ATOMIC_RELAXED)
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define dma_wmb() ((void)0)
#define read_poll_timeout(op,v,cond,delay,timeout,sleep,addr) ((void)(delay),(void)(timeout),(void)(sleep),poll_tick(addr),(v)=op(addr),poll_error?poll_error:((cond)?0:-ETIMEDOUT))
struct ieee80211_hw {unsigned queues;bool stopped;};
struct rtl_ax_io {void *ctx;u32(*read32)(void*,unsigned);u8(*read8)(void*,unsigned);void(*delay_us)(void*,unsigned);};
struct rtl_ax_tx {const struct rtl_ax_io *io;int lock;bool running,paused,queues_paused,dfs_blocked,pe_active,queues_stopped,checksum_enabled,credit_wake;
 void *rx;bool peer_offloaded[128];struct {bool active;}ni;
 struct ieee80211_hw *hw;unsigned occupied,pe_producer,pe_command,queue_stops,queue_wakes,stopped;struct {void *memory;}pe;u32 scheduler_saved;};
typedef struct {unsigned value;} atomic_t;
static unsigned tasklet_schedules;
static unsigned atomic_fetch_or(unsigned v,atomic_t*p){unsigned old=p->value;p->value|=v;return old;}
static void atomic_andnot(unsigned v,atomic_t*p){p->value&=~v;}
static unsigned atomic_cmpxchg(atomic_t*p,unsigned old,unsigned v){unsigned was=p->value;if(was==old)p->value=v;return was;}
struct tasklet_struct {int unused;};
static void tasklet_schedule(struct tasklet_struct*t){(void)t;tasklet_schedules++;}
struct hrtimer {unsigned expires,dispatched;};
static void rtl_ax_tx_batch_timer(struct hrtimer *timer){timer->dispatched++;}
static unsigned wake_cpu;
static bool serving_softirq;
static unsigned smp_processor_id(void){return wake_cpu;}
static bool in_serving_softirq(void){return serving_softirq;}
#define NSEC_PER_USEC 1000
#define HRTIMER_MODE_REL_PINNED_SOFT 1
#define ns_to_ktime(ns) (ns)
static void hrtimer_start(struct hrtimer *timer,unsigned expires,int mode){
 assert(mode==HRTIMER_MODE_REL_PINNED_SOFT);timer->expires=expires;
}
struct rtl_ax_station {unsigned macid;unsigned long ba_retry[8];};
struct ieee80211_sta {void *drv_priv;bool wme;struct {struct {bool ht_supported;}ht_cap;struct {bool vht_supported;}vht_cap;struct {bool has_he;}he_cap;}deflink;};
struct rtl_ax_ap {atomic_t txq_schedule;struct tasklet_struct tx_tasklet;struct hrtimer tx_batch_timer;unsigned tx_batch_cpu;
 struct {unsigned bandwidth;}channel;struct ieee80211_hw *hw;struct rtl_ax_tx *tx;bool monitor_started;bool peer_authorized[128];struct {unsigned aggregation[8],stopping;}tx_ba[128];};
#define NL80211_IFTYPE_AP 3
struct ieee80211_vif {unsigned type;};
struct ieee80211_txq {struct ieee80211_sta *sta;struct ieee80211_vif *vif;unsigned tid;};
#define IEEE80211_AC_VO 3
#define HZ 100
#define jiffies 1000UL
#define time_after_eq(a,b) ((long)((a)-(b))>=0)
static unsigned ba_requests;
static bool inject_wake;
static void rtl_ax_wake_tx_queue(struct ieee80211_hw*,struct ieee80211_txq*);
static int ieee80211_start_tx_ba_session(struct ieee80211_sta *s,unsigned tid,unsigned timeout){(void)s;(void)tid;(void)timeout;ba_requests++;return 0;}
struct ieee80211_tx_control {void *sta;};struct sk_buff {int unused;};
#define from_tasklet(p,t,member) ((struct rtl_ax_ap *)((char *)(t)-offsetof(struct rtl_ax_ap,member)))
static int rx_pause_error;static unsigned retired;
static bool rx_admitted=true;
static int rtl_ax_rx_pe_pause(void *rx){(void)rx;return rx_pause_error;}
static void rtl_ax_rx_pe_open(void *rx){(void)rx;}
static void rtl_ax_rx_uplink_disable(void *rx,unsigned id){(void)rx;(void)id;rx_admitted=false;}
static void rtl_ax_rx_uplink_retire(void *rx,unsigned id){(void)rx;(void)id;retired++;}
static unsigned remaining,accepted,dropped,stops,wakes,syncs,inflight;
static bool empty_scheduled;
static bool check_credit_during_sync,mac_busy;static int poll_error;
static unsigned polls,fail_poll;
static struct rtl_ax_tx *active;
static struct rtl_ax_ap *active_radio;
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){(void)hw;return active_radio;}
static struct ieee80211_txq queue;static struct sk_buff skb;
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static void poll_tick(void *address){
 polls++;
 if(poll_error||polls==fail_poll)return;
 if(address==(u8 *)active->pe.memory+PE_PROBE_MAILBOX+36)
  put_unaligned_le32(active->pe_producer,address);
 else if(address==(u8 *)active->pe.memory+PE_PROBE_MAILBOX+16)
  put_unaligned_le32(get_unaligned_le32((u8 *)active->pe.memory+PE_PROBE_MAILBOX+12),address);
}
static void ieee80211_stop_queues(struct ieee80211_hw *hw){assert(hw);hw->stopped=true;stops++;}
static void ieee80211_wake_queues(struct ieee80211_hw *hw){assert(hw);hw->stopped=false;wakes++;}
static bool ieee80211_queue_stopped(struct ieee80211_hw *hw,unsigned ac){assert(!ac);return hw->stopped;}
static void ieee80211_txq_schedule_start(struct ieee80211_hw *hw,unsigned ac){(void)hw;assert(!ac);}
static struct ieee80211_txq *ieee80211_next_txq(struct ieee80211_hw *hw,unsigned ac){(void)ac;if(empty_scheduled){empty_scheduled=false;return &queue;}return !hw->stopped&&remaining?&queue:0;}
static struct sk_buff *ieee80211_tx_dequeue(struct ieee80211_hw *hw,struct ieee80211_txq *q){(void)hw;(void)q;if(!remaining)return 0;remaining--;return &skb;}
static void ieee80211_return_txq(struct ieee80211_hw *hw,struct ieee80211_txq *q,bool force){(void)hw;(void)q;(void)force;}
static void ieee80211_txq_schedule_end(struct ieee80211_hw *hw,unsigned ac){
 (void)ac;
 if(inject_wake){inject_wake=false;unsigned before=tasklet_schedules;
  for(unsigned i=0;i<1000;i++)rtl_ax_wake_tx_queue(hw,&queue);
  assert(tasklet_schedules==before); /* No other CPU queues the running tasklet. */
 }
}
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
static u32 read32(void *ctx,unsigned reg){(void)ctx;return reg==0xc398?0x3ffff:0;}
static u8 read8(void *ctx,unsigned reg){(void)ctx;(void)reg;return mac_busy?BIT(7):0;}
static void delay(void *ctx,unsigned us){(void)ctx;(void)us;}
static void rtl_ax_update32(const struct rtl_ax_io *io,unsigned reg,unsigned mask,unsigned value){(void)io;(void)reg;(void)mask;(void)value;}
static int guard(struct rtl_ax_tx *tx){int ret;
'''
guard = block(function(tx, 'rtl_ax_tx_submit'), 'if (!tx->running || tx->dfs_blocked)')
report = block(function(tx, 'rtl_ax_tx_report'), 'if (tx->queues_stopped && tx->running')
callbacks = r'''
 return 0;unmap:return ret;}
static void credit_complete(struct rtl_ax_tx *tx,struct ieee80211_hw *hw){
'''
callbacks2 = r'''
}
static void synchronize_net(void){
 syncs++;assert(active->hw->stopped);
 if(inflight){assert(!guard(active));accepted+=inflight;inflight=0;}
 if(check_credit_during_sync){credit_complete(active,active->hw);assert(active->hw->stopped);}
}
static void rtl_ax_monitor_tx(struct ieee80211_hw *hw,struct ieee80211_tx_control *c,struct sk_buff *p){
 (void)hw;(void)c;(void)p;if(guard(active))dropped++;else accepted++;
}
'''
case = r'''
int main(void){
 u8 memory[0x20000]={};struct ieee80211_hw hw={.queues=1};
 struct rtl_ax_io io={.read32=read32,.read8=read8,.delay_us=delay};
 struct rtl_ax_tx tx={.io=&io,.running=true,.pe={memory}};
#ifdef EXPECT_OLD
 tx.hw=&hw;
#else
 rtl_ax_tx_bind_hw(&tx,&hw);
#endif
 struct rtl_ax_ap radio={.hw=&hw,.tx=&tx,.monitor_started=true};active=&tx;active_radio=&radio;
 remaining=32;inflight=1;assert(!rtl_ax_tx_pause(&tx));run_tx_queues(&radio);
 unsigned kept=remaining,loss=dropped;
#ifdef EXPECT_OLD
 assert(!kept&&loss==32&&tx.stopped==32&&!syncs);
#else
 assert(kept==32&&!loss&&accepted==1&&syncs==1&&hw.stopped&&tx.queues_paused);
#endif
 rtl_ax_tx_open(&tx);run_tx_queues(&radio);
#ifndef EXPECT_OLD
 assert(!remaining&&!dropped&&accepted==33&&!hw.stopped&&!tx.queues_paused);
 /* RPQ may finish during the grace period; it must not reopen dequeue. */
 tx.queues_stopped=true;tx.occupied=64;check_credit_during_sync=true;
 assert(!rtl_ax_tx_pause(&tx)&&hw.stopped);check_credit_during_sync=false;
 rtl_ax_tx_open(&tx);assert(!tx.queues_stopped&&!hw.stopped);
 /* A real credit stop survives pause/open until its normal wake threshold. */
 tx.queues_stopped=true;tx.occupied=200;assert(!rtl_ax_tx_pause(&tx));
 rtl_ax_tx_open(&tx);assert(tx.queues_stopped&&hw.stopped);
 tx.occupied=128;credit_complete(&tx,&hw);assert(!tx.queues_stopped&&!hw.stopped);
 /* A failed PE pause keeps packets queued and preserves the error. */
 tx.pe_active=true;poll_error=-ETIMEDOUT;remaining=7;
 assert(rtl_ax_tx_pause(&tx)==-ETIMEDOUT);run_tx_queues(&radio);
 assert(remaining==7&&!dropped&&hw.stopped);poll_error=0;rtl_ax_tx_open(&tx);
 run_tx_queues(&radio);assert(!remaining&&!dropped);
 /* RX BA pause failure must roll back only an originally running radio. */
 tx.ni.active=true;tx.peer_offloaded[7]=true;tx.pe_active=true;
 for(unsigned failure=1;failure<=4;failure++) {
  remaining=7;polls=0;fail_poll=failure<=2?failure:0;
  tx.pe_producer++;
  rx_pause_error=failure==3?-ETIMEDOUT:0;mac_busy=failure==4;
  assert(rtl_ax_tx_rx_retire(&tx,7)==-ETIMEDOUT);
  assert(tx.running&&!tx.queues_paused&&!hw.stopped&&!retired&&!rx_admitted);
  assert(tx.peer_offloaded[7]);
  run_tx_queues(&radio);assert(!remaining&&!dropped);
 }
 fail_poll=0;rx_pause_error=0;mac_busy=false;assert(!rtl_ax_tx_rx_retire(&tx,7));
 assert(retired==1&&!tx.peer_offloaded[7]&&tx.running&&!hw.stopped);
 /* Never reopen a radio that was already paused (e.g. failed RFK). */
 assert(!rtl_ax_tx_pause(&tx));rx_pause_error=-ETIMEDOUT;
 assert(rtl_ax_tx_rx_retire(&tx,7)==-ETIMEDOUT);
 assert(!tx.running&&tx.queues_paused&&hw.stopped&&retired==1);
 rx_pause_error=0;rtl_ax_tx_open(&tx);
 /* Rollback must preserve an independent DFS block and a WP-credit stop. */
 tx.dfs_blocked=true;tx.queues_stopped=true;tx.occupied=200;rx_pause_error=-ETIMEDOUT;
 assert(rtl_ax_tx_rx_retire(&tx,7)==-ETIMEDOUT);
 assert(tx.running&&tx.dfs_blocked&&tx.queues_stopped&&hw.stopped);
 tx.dfs_blocked=false;rx_pause_error=0;tx.occupied=0;rtl_ax_tx_open(&tx);
 /* A BA teardown can reschedule an empty TXQ: do not recreate it. */
 struct rtl_ax_station station={.macid=1};
 struct ieee80211_sta sta={.drv_priv=&station,.wme=true,.deflink.he_cap.has_he=true};
 struct ieee80211_vif ap={.type=NL80211_IFTYPE_AP};
 queue.sta=&sta;queue.vif=&ap;queue.tid=0;radio.peer_authorized[1]=true;
 remaining=0;empty_scheduled=true;run_tx_queues(&radio);
 assert(ba_requests==0&&!station.ba_retry[0]);
 remaining=1;run_tx_queues(&radio);
 assert(ba_requests==1&&!remaining&&station.ba_retry[0]==jiffies+HZ);
 empty_scheduled=true;run_tx_queues(&radio);assert(ba_requests==1);
 /* Wakes while idle coalesce into one scheduled callback. */
 radio.txq_schedule.value=0;unsigned scheduled=tasklet_schedules;
 for(unsigned i=0;i<100;i++)rtl_ax_wake_tx_queue(&hw,&queue);
 assert(tasklet_schedules==scheduled+1&&radio.txq_schedule.value==3);
 /* A wake after the drain, before completion, must cause exactly one rerun. */
 inject_wake=true;rtl_ax_tx_queues(&radio.tx_tasklet);
 assert(tasklet_schedules==scheduled+2&&radio.txq_schedule.value==3);
 remaining=1;rtl_ax_tx_queues(&radio.tx_tasklet);
 assert(radio.txq_schedule.value==0&&!remaining);
 /* A wake after ownership retires starts a new pass; stopped hardware does not. */
 rtl_ax_wake_tx_queue(&hw,&queue);assert(tasklet_schedules==scheduled+3);
 radio.monitor_started=false;rtl_ax_tx_queues(&radio.tx_tasklet);
 assert(radio.txq_schedule.value==0);
 rtl_ax_wake_tx_queue(&hw,&queue);assert(tasklet_schedules==scheduled+3);
 radio.monitor_started=true;
 radio.channel.bandwidth=3;radio.tx_ba[1].aggregation[0]=true;
 wake_cpu=RTL_AX_HE160_TX_BATCH_ALT_CPU;
 rtl_ax_wake_tx_queue(&hw,&queue);
 assert(tasklet_schedules==scheduled+3&&radio.txq_schedule.value==3);
 assert(radio.tx_batch_timer.expires==RTL_AX_HE160_TX_BATCH_US*NSEC_PER_USEC);
 assert(radio.tx_batch_cpu==RTL_AX_HE160_TX_BATCH_CPU);
 run_tx_queues(&radio);
 serving_softirq=true;
 rtl_ax_wake_tx_queue(&hw,&queue);
 assert(radio.tx_batch_cpu==RTL_AX_HE160_TX_BATCH_ALT_CPU);
 wake_cpu=RTL_AX_HE160_TX_BATCH_CPU;
 rtl_ax_wake_tx_queue(&hw,&queue);
 assert(radio.tx_batch_cpu==RTL_AX_HE160_TX_BATCH_ALT_CPU);
 /* Returning real WP credits dispatches on the same TX CPU without a new timer. */
 run_tx_queues(&radio);
 radio.tx_batch_timer.expires=0;
 tx.queues_stopped=true;tx.occupied=128;credit_complete(&tx,&hw);
 assert(tx.credit_wake&&!tx.queues_stopped);
 rtl_ax_wake_tx_queue(&hw,&queue);
 assert(!tx.credit_wake&&radio.tx_batch_timer.dispatched==1&&!radio.tx_batch_timer.expires);
 assert(radio.tx_batch_cpu==RTL_AX_HE160_TX_BATCH_CPU);
 /* Probe/teardown without a registered mac80211 object cannot touch its queues. */
 rtl_ax_tx_bind_hw(&tx,0);tx.pe_active=false;unsigned old_stops=stops,old_wakes=wakes;
 assert(!rtl_ax_tx_pause(&tx));rtl_ax_tx_open(&tx);assert(stops==old_stops&&wakes==old_wakes);
#endif
 printf("{\"queued_during_pause\":%u,\"dropped_during_pause\":%u,\"network_grace_periods\":%u,\"queue_stops\":%u,\"queue_wakes\":%u}\n",kept,loss,syncs,stops,wakes);
}
'''
with tempfile.TemporaryDirectory(prefix='pause-queue-', dir=root / 'artifacts') as temp:
    p = Path(temp)
    schedule_bits = '\n'.join(line for line in monitor.splitlines()
                              if line.startswith(('#define RTL_AX_TXQ_', '#define RTL_AX_HE160_TX_BATCH_'))) + '\n'
    source = common + schedule_bits + guard + callbacks + report + callbacks2
    if not args.before:
        source += function(tx, 'rtl_ax_tx_bind_hw')
    source += function((driver / 'tx.c').read_text(), 'rtl_ax_tx_take_credit_wake')
    source += function(tx, 'rtl_ax_tx_pause') + function(tx, 'rtl_ax_tx_open') + function((driver / 'ni-test.h').read_text(), 'rtl_ax_tx_rx_retire') + function(monitor, 'rtl_ax_tx_queues') + function(monitor, 'rtl_ax_wake_tx_queue') + r'''
static void run_tx_queues(struct rtl_ax_ap *radio){radio->txq_schedule.value=1;rtl_ax_tx_queues(&radio->tx_tasklet);}
''' + case
    (p / 'case.c').write_text(source)
    command = ['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-parameter', '-fsanitize=address,undefined']
    if args.before:
        command.append('-DEXPECT_OLD')
    subprocess.run(command + [str(p / 'case.c'), '-o', str(p / 'case')], check=True)
    subprocess.run([str(p / 'case')], check=True)
