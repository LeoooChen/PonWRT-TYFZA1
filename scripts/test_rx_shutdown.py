#!/usr/bin/env python3
"""Run actual NI stop and QM poll tail under controlled two-thread races.

Spinlocks/IRQ state, NAPI ownership and synchronize_net's grace-period wait
are modeled. This is a driver interleaving regression, not a kernel emulator.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
relative = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision')
args = parser.parse_args()
source = subprocess.check_output(['git', 'show', f'{args.revision}:{relative}'], cwd=root, text=True) if args.revision else (root / relative).read_text()


def block(start):
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


stop = block(source.index('static void rtl_engine_stop('))
poll_start = source.index('static int rtl_poll(')
branch = block(source.index('if (priv->qm_rx)', poll_start))
test = r'''
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
struct spinlock {pthread_mutex_t mutex;};
struct napi_struct {int unused;};
struct rtl_qm_rx {bool stopped;};
struct rtl_xram {bool qm_rx,dma_tx,rx_active;struct spinlock rx_lock;
 struct napi_struct napi;struct rtl_qm_rx qm;int timer,rx_irq,dma;unsigned int mask;};
static pthread_mutex_t gate=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event=PTHREAD_COND_INITIALIZER;
static bool owned,window,attempted,masked,sync_wait,finished,poll_live;
static unsigned int scenario,grace_periods,rearms;
static _Thread_local bool closing,irq_off,held;
static void signal_event(void) {assert(!pthread_cond_broadcast(&event));}
static void wait_event(void) {assert(!pthread_cond_wait(&event,&gate));}
static void wait_for_stop(void) {
 assert(!pthread_mutex_lock(&gate));window=true;signal_event();
 while(!sync_wait)wait_event();
 assert(!finished);assert(!pthread_mutex_unlock(&gate));
}
static void lock(struct spinlock *p) {
 if(closing) {
  assert(!pthread_mutex_lock(&gate));attempted=true;signal_event();
  assert(!pthread_mutex_unlock(&gate));
 } else if(scenario==2) wait_for_stop();
 assert(!pthread_mutex_lock(&p->mutex));held=true;
 if(!closing && scenario==3) {
  assert(!pthread_mutex_lock(&gate));window=true;signal_event();
  while(!attempted)wait_event();
  assert(!pthread_mutex_unlock(&gate));
 }
}
static void unlock(struct spinlock *p) {assert(held);held=false;assert(!pthread_mutex_unlock(&p->mutex));}
#define spin_lock_irqsave(p,f) do{(f)=irq_off;irq_off=true;lock(p);}while(0)
#define spin_unlock_irqrestore(p,f) do{unlock(p);irq_off=(f);}while(0)
#define XRAM_CFG 0xaa28
#define CPU_DISABLE 0x201
static void rtl_update(struct rtl_xram *p,unsigned reg,unsigned mask,unsigned value) {
 if(reg==0x6110) {
  assert(held && irq_off);
  if(value) {assert(!closing && p->rx_active && !p->qm.stopped);rearms++;}
  p->mask=(p->mask&~mask)|(value&mask);
 }
}
static void hrtimer_cancel(int *timer) {(void)timer;assert(closing);}
static void synchronize_irq(int irq) {
 (void)irq;assert(!irq_off && !held);
 assert(!pthread_mutex_lock(&gate));masked=true;signal_event();assert(!pthread_mutex_unlock(&gate));
}
static void napi_disable(struct napi_struct *napi) {
 (void)napi;assert(!irq_off && !held);
 assert(!pthread_mutex_lock(&gate));while(owned)wait_event();assert(!pthread_mutex_unlock(&gate));
}
static void __attribute__((unused)) synchronize_net(void) {
 assert(closing && !irq_off && !held);
 assert(!pthread_mutex_lock(&gate));sync_wait=true;grace_periods++;signal_event();
 while(poll_live)wait_event();
 assert(!pthread_mutex_unlock(&gate));
}
static void rtl_qm_rx_stop(struct rtl_qm_rx *qm) {
 assert(!pthread_mutex_lock(&gate));
 assert(!poll_live && "QM stop must wait beyond NAPI ownership release for the poll tail");
 assert(!pthread_mutex_unlock(&gate));qm->stopped=true;
}
static void rtl_dma_tx_stop(int *dma) {*dma=0;}
static int rtl_qm_rx_poll(struct rtl_qm_rx *qm,struct napi_struct *napi,int budget) {
 (void)qm;(void)napi;(void)budget;assert(!irq_off && !held);
 if(scenario==0) {
  assert(!pthread_mutex_lock(&gate));window=true;signal_event();
  while(!masked)wait_event();assert(!pthread_mutex_unlock(&gate));
 }
 return 1;
}
static int napi_complete_done(struct napi_struct *napi,int count) {
 (void)napi;(void)count;
 assert(!irq_off && !held && "NAPI completion may dispatch GRO into the stack");
 assert(!pthread_mutex_lock(&gate));owned=false;signal_event();assert(!pthread_mutex_unlock(&gate));
 if(scenario==1) wait_for_stop();
 return 1;
}
'''
test += stop + '\nstatic int poll_completion(struct rtl_xram *priv,int budget) {\nstruct napi_struct *napi=&priv->napi;int packets=0;\n' + branch + '\nreturn packets;\n}\n'
test += r'''
static void *close_engine(void *arg) {
 struct rtl_xram *priv=arg;closing=true;
 assert(!pthread_mutex_lock(&gate));while(!window)wait_event();assert(!pthread_mutex_unlock(&gate));
 rtl_engine_stop(priv);
 assert(!priv->rx_active && !priv->mask && priv->qm.stopped && !priv->dma);
 /* A subsequent open must never overlap the previous poll's rearm tail. */
 assert(!pthread_mutex_lock(&gate));assert(!poll_live);assert(!pthread_mutex_unlock(&gate));
 assert(!pthread_mutex_lock(&priv->rx_lock.mutex));
 priv->qm.stopped=false;priv->rx_active=true;priv->mask=255;
 assert(!pthread_mutex_unlock(&priv->rx_lock.mutex));
 assert(!pthread_mutex_lock(&gate));finished=true;signal_event();assert(!pthread_mutex_unlock(&gate));
 return NULL;
}
int main(void) {
 unsigned cases=0;
 for(unsigned repeat=0;repeat<25;repeat++)for(unsigned index=0;index<4;index++) {
  scenario=(index+1)%4;
  struct rtl_xram priv={.qm_rx=true,.dma_tx=true,.rx_active=true,.mask=255,.dma=1};
  pthread_t closer;
  owned=poll_live=true;window=attempted=masked=sync_wait=finished=false;
  grace_periods=rearms=0;irq_off=held=closing=false;
  assert(!pthread_mutex_init(&priv.rx_lock.mutex,NULL));
  assert(!pthread_create(&closer,NULL,close_engine,&priv));
  assert(poll_completion(&priv,64)==1);assert(!irq_off && !held);
  assert(!pthread_mutex_lock(&gate));poll_live=false;signal_event();assert(!pthread_mutex_unlock(&gate));
  assert(!pthread_join(closer,NULL));
  assert(finished && grace_periods==1 && priv.rx_active && priv.mask==255 && !priv.qm.stopped);
  assert(rearms==(scenario==3));
  assert(!pthread_mutex_destroy(&priv.rx_lock.mutex));cases++;
 }
 printf("NI stop/poll: %u controlled races passed (stop during RX, completion, before rearm, during rearm; reopen after tail)\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='rx-stop-test-') as directory:
    directory = Path(directory)
    cfile, binary = directory / 'rx-stop.c', directory / 'rx-stop'
    cfile.write_text(test)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-misleading-indentation',
                    '-O1', '-g', '-pthread', '-fsanitize=address,undefined', str(cfile), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
    print(result.stdout + result.stderr, end='')
    if result.returncode:
        raise SystemExit(1)
if not args.revision:
    (root / 'logs').mkdir(exist_ok=True)
    (root / 'logs/rx-shutdown-test.json').write_text(json.dumps({'actual_stop_and_poll_branch': True,
        'controlled_threads': 2, 'interleavings': 4, 'repetitions': 25,
        'completion_local_irqs_enabled': True, 'both_interleavings_passed': True,
        'shutdown_waits_for_poll_tail': True, 'subsequent_reopen': True,
        'hardware_access': False, 'synchronize_net': 'modeled grace-period contract'}, indent=2)+'\n')
