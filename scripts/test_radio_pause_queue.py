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
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define dma_wmb() ((void)0)
#define read_poll_timeout(op,v,cond,delay,timeout,sleep,addr) ((void)(delay),(void)(timeout),(void)(sleep),(v)=op(addr),poll_error?poll_error:((cond)?0:-ETIMEDOUT))
struct ieee80211_hw {unsigned queues;bool stopped;};
struct rtl_ax_io {void *ctx;u32(*read32)(void*,unsigned);u8(*read8)(void*,unsigned);void(*delay_us)(void*,unsigned);};
struct rtl_ax_tx {const struct rtl_ax_io *io;int lock;bool running,paused,queues_paused,dfs_blocked,pe_active,queues_stopped,checksum_enabled;
 struct ieee80211_hw *hw;unsigned occupied,pe_producer,pe_command,queue_stops,queue_wakes,stopped;struct {void *memory;}pe;u32 scheduler_saved;};
struct tasklet_struct {int unused;};
struct rtl_ax_ap {struct tasklet_struct tx_tasklet;struct ieee80211_hw *hw;bool monitor_started;};
struct ieee80211_txq {void *sta;};struct ieee80211_tx_control {void *sta;};struct sk_buff {int unused;};
#define from_tasklet(p,t,member) ((struct rtl_ax_ap *)((char *)(t)-offsetof(struct rtl_ax_ap,member)))
static unsigned remaining,accepted,dropped,stops,wakes,syncs,inflight;
static bool check_credit_during_sync;static int poll_error;
static struct rtl_ax_tx *active;
static struct ieee80211_txq queue;static struct sk_buff skb;
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static void ieee80211_stop_queues(struct ieee80211_hw *hw){assert(hw);hw->stopped=true;stops++;}
static void ieee80211_wake_queues(struct ieee80211_hw *hw){assert(hw);hw->stopped=false;wakes++;}
static bool ieee80211_queue_stopped(struct ieee80211_hw *hw,unsigned ac){assert(!ac);return hw->stopped;}
static void ieee80211_txq_schedule_start(struct ieee80211_hw *hw,unsigned ac){(void)hw;assert(!ac);}
static struct ieee80211_txq *ieee80211_next_txq(struct ieee80211_hw *hw,unsigned ac){(void)ac;return !hw->stopped&&remaining?&queue:0;}
static struct sk_buff *ieee80211_tx_dequeue(struct ieee80211_hw *hw,struct ieee80211_txq *q){(void)hw;(void)q;if(!remaining)return 0;remaining--;return &skb;}
static void ieee80211_return_txq(struct ieee80211_hw *hw,struct ieee80211_txq *q,bool force){(void)hw;(void)q;(void)force;}
static void ieee80211_txq_schedule_end(struct ieee80211_hw *hw,unsigned ac){(void)hw;(void)ac;}
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
static u32 read32(void *ctx,unsigned reg){(void)ctx;return reg==0xc398?0x3ffff:0;}
static u8 read8(void *ctx,unsigned reg){(void)ctx;(void)reg;return 0;}
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
 syncs++;assert(active->hw->stopped&&active->running);
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
 struct rtl_ax_ap radio={.hw=&hw,.monitor_started=true};active=&tx;
 remaining=32;inflight=1;assert(!rtl_ax_tx_pause(&tx));rtl_ax_tx_queues(&radio.tx_tasklet);
 unsigned kept=remaining,loss=dropped;
#ifdef EXPECT_OLD
 assert(!kept&&loss==32&&tx.stopped==32&&!syncs);
#else
 assert(kept==32&&!loss&&accepted==1&&syncs==1&&hw.stopped&&tx.queues_paused);
#endif
 rtl_ax_tx_open(&tx);rtl_ax_tx_queues(&radio.tx_tasklet);
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
 assert(rtl_ax_tx_pause(&tx)==-ETIMEDOUT);rtl_ax_tx_queues(&radio.tx_tasklet);
 assert(remaining==7&&!dropped&&hw.stopped);poll_error=0;rtl_ax_tx_open(&tx);
 rtl_ax_tx_queues(&radio.tx_tasklet);assert(!remaining&&!dropped);
 /* Probe/teardown without a registered mac80211 object cannot touch its queues. */
 rtl_ax_tx_bind_hw(&tx,0);tx.pe_active=false;unsigned old_stops=stops,old_wakes=wakes;
 assert(!rtl_ax_tx_pause(&tx));rtl_ax_tx_open(&tx);assert(stops==old_stops&&wakes==old_wakes);
#endif
 printf("{\"queued_during_pause\":%u,\"dropped_during_pause\":%u,\"network_grace_periods\":%u,\"queue_stops\":%u,\"queue_wakes\":%u}\n",kept,loss,syncs,stops,wakes);
}
'''
with tempfile.TemporaryDirectory(prefix='pause-queue-', dir=root / 'artifacts') as temp:
    p = Path(temp)
    source = common + guard + callbacks + report + callbacks2
    if not args.before:
        source += function(tx, 'rtl_ax_tx_bind_hw')
    source += function(tx, 'rtl_ax_tx_pause') + function(tx, 'rtl_ax_tx_open') + function(monitor, 'rtl_ax_tx_queues') + case
    (p / 'case.c').write_text(source)
    command = ['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined']
    if args.before:
        command.append('-DEXPECT_OLD')
    subprocess.run(command + [str(p / 'case.c'), '-o', str(p / 'case')], check=True)
    subprocess.run([str(p / 'case')], check=True)
