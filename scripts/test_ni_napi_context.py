#!/usr/bin/env python3
"""Execute the exact QM branch and kernel completion/GRO functions.

The protocol-stack endpoint records entry context. This verifies IRQ context
at the API boundary, not a specific stack deadlock or the reporter's stall.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', type=Path, required=True)
parser.add_argument('--revision')
args = parser.parse_args()
relative = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c'
driver = subprocess.check_output(['git', 'show', f'{args.revision}:{relative}'], cwd=root, text=True) if args.revision else (root / relative).read_text()


def block(source, start):
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def function(relative, name):
    source = driver if relative == 'driver' else (args.kernel / relative).read_text()
    match = re.search(r'(?m)^(?:static\s+)?(?:inline\s+)?[\w *]+\b' +
                      name + r'\([^;{}]*\)\s*\{', source)
    return block(source, match.start())


poll = function('driver', 'rtl_poll')
qm = block(poll, poll.index('if (priv->qm_rx)'))
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define unlikely(x) (x)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,y) ((x)=(y))
#define WARN_ON_ONCE(x) assert(!(x))
#define NAPIF_STATE_NPSVC 1UL
#define NAPIF_STATE_IN_BUSY_POLL 2UL
#define NAPIF_STATE_SCHED 4UL
#define NAPIF_STATE_MISSED 8UL
#define NAPIF_STATE_SCHED_THREADED 16UL
#define NAPIF_STATE_PREFER_BUSY_POLL 32UL
#define HRTIMER_MODE_REL_PINNED 0
#define ns_to_ktime(x) (x)
struct list_head {int empty;};
struct gro_node {unsigned rx_count,bitmask;struct list_head rx_list;};
struct napi_struct {unsigned long state;struct gro_node gro;
 unsigned defer_hard_irqs_count,timeout,defer;struct list_head poll_list;int list_owner,timer;};
struct rtl_xram {bool qm_rx,rx_active;unsigned rx_lock;int qm;};
static unsigned irq_off,held,deliveries,bad_context,rearms,poll_result,reschedules,timers;
#define local_irq_save(f) do {(f)=irq_off;irq_off=1;}while(0)
#define local_irq_restore(f) do {irq_off=(f);}while(0)
#define spin_lock_irqsave(p,f) do {local_irq_save(f);assert(!*(p));*(p)=held=1;}while(0)
#define spin_unlock_irqrestore(p,f) do {assert(*(p));*(p)=held=0;local_irq_restore(f);}while(0)
static bool list_empty(struct list_head *p) {return p->empty;}
#define INIT_LIST_HEAD(p) ((p)->empty=1)
static void list_del_init(struct list_head *p) {INIT_LIST_HEAD(p);}
static void netif_receive_skb_list_internal(struct list_head *p) {
 (void)p;deliveries++;bad_context+=irq_off;
 assert(!irq_off && !held && "GRO delivery must run with IRQ enabled and rx_lock released");
}
static void gro_flush(struct gro_node *g,bool old) {(void)g;(void)old;}
static unsigned napi_get_gro_flush_timeout(struct napi_struct *n) {return n->timeout;}
static unsigned napi_get_defer_hard_irqs(struct napi_struct *n) {return n->defer;}
static bool try_cmpxchg(unsigned long *p,unsigned long *expected,unsigned long value) {
 if(*p!=*expected){*expected=*p;return false;}*p=value;return true;
}
static void __napi_schedule(struct napi_struct *n) {(void)n;reschedules++;}
static void hrtimer_start(int *t,unsigned long when,int mode) {(void)t;(void)when;(void)mode;timers++;}
static int rtl_qm_rx_poll(int *qm,struct napi_struct *n,int budget) {
 (void)qm;(void)n;assert(poll_result<=(unsigned)budget);return poll_result;
}
static void rtl_update(struct rtl_xram *p,unsigned reg,unsigned mask,unsigned val) {
 assert(p->rx_lock && irq_off && reg==0x6110 && mask==255 && val==255);rearms++;
}
'''
source += function('include/net/gro.h', 'gro_normal_list') + '\n'
source += function('include/net/gro.h', 'gro_flush_normal') + '\n'
source += function('net/core/dev.c', 'napi_complete_done') + '\n'
source += 'static int rtl_poll_qm_fragment(struct rtl_xram *priv,struct napi_struct *napi,int budget) {\nint packets=0;\n'
source += qm + '\nreturn -1;\n}\n'
source += r'''
int main(void) {
 unsigned cases=0;
 for(unsigned full=0;full<3;full++)for(unsigned batched=0;batched<2;batched++)
 for(unsigned active=0;active<2;active++) {
  struct rtl_xram priv={.qm_rx=true,.rx_active=active};
  struct napi_struct n={.state=NAPIF_STATE_SCHED,.poll_list={.empty=1},.gro={.rx_count=batched}};
  irq_off=held=deliveries=bad_context=rearms=0;poll_result=full==1?64:full==2?0:1;
  assert(rtl_poll_qm_fragment(&priv,&n,full==2?0:64)==(int)poll_result);
  assert(!irq_off && !held && !priv.rx_lock);
  assert(deliveries==(!full && batched));assert(!bad_context);
  assert(rearms==(!full && active));cases++;
 }
 /* Kernel completion false paths must never rearm the hardware. */
 const unsigned long states[]={NAPIF_STATE_NPSVC,NAPIF_STATE_IN_BUSY_POLL,NAPIF_STATE_MISSED,0};
 for(unsigned mode=0;mode<4;mode++) {
  struct rtl_xram priv={.qm_rx=true,.rx_active=true};
  struct napi_struct n={.state=NAPIF_STATE_SCHED|states[mode],.poll_list={.empty=1},
   .gro={.rx_count=1},.timeout=mode==3?1:0,.defer=mode==3?1:0};
  irq_off=held=deliveries=bad_context=rearms=reschedules=timers=0;poll_result=1;
  assert(rtl_poll_qm_fragment(&priv,&n,64)==1);
  assert(!rearms && !bad_context && !irq_off && !held);
  assert(deliveries==(mode>=2));assert(reschedules==(mode==2));assert(timers==(mode==3));
  cases++;
 }
 printf("NI NAPI context: %u cases passed (short/full/zero budget, stopped RX, NPSVC, busy poll, MISSED and deferred IRQ)\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='ni-napi-context-') as directory:
    directory = Path(directory)
    unit, binary = directory / 'test.c', directory / 'test'
    unit.write_text(source)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-O1', '-g',
                    '-fsanitize=address,undefined', str(unit), '-o', str(binary)], check=True, timeout=30)
    run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
    print(run.stdout + run.stderr, end='')
    if run.returncode:
        raise SystemExit(1)
