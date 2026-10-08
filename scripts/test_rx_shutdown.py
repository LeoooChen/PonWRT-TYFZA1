#!/usr/bin/env python3
"""Run actual NI stop and QM poll-completion code with late NAPI completion."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
relative = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision')
args = parser.parse_args()
source = subprocess.check_output(['git', 'show', f'{args.revision}:{relative}'], cwd=root, text=True) if args.revision else (root / relative).read_text()


def block(start):
    opening = source.index('{', start)
    end = opening + 1
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
struct spinlock {pthread_mutex_t mutex;bool held;};
struct napi_struct {};
struct rtl_qm_rx {bool stopped;};
struct rtl_xram {bool qm_rx,dma_tx,rx_active;struct spinlock rx_lock;struct napi_struct napi;struct rtl_qm_rx qm;int timer,rx_irq,dma;unsigned int mask;};
static pthread_t closer;
static pthread_mutex_t gate=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event=PTHREAD_COND_INITIALIZER;
static bool owned,window,attempted,masked,finished;
static unsigned int scenario;
static void lock(struct spinlock *p) {
    if(pthread_equal(pthread_self(),closer)) {
        pthread_mutex_lock(&gate);attempted=true;pthread_cond_broadcast(&event);pthread_mutex_unlock(&gate);
    }
    assert(!pthread_mutex_lock(&p->mutex));p->held=true;
}
static void unlock(struct spinlock *p) {p->held=false;assert(!pthread_mutex_unlock(&p->mutex));}
#define spin_lock_irqsave(p,f) do{(f)=0;lock(p);}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(f);unlock(p);}while(0)
#define XRAM_CFG 0xaa28
#define CPU_DISABLE 0x201
static struct rtl_xram *current;
static void rtl_update(struct rtl_xram *p,unsigned int reg,unsigned int mask,unsigned int value) {
    if(reg==0x6110) p->mask=(p->mask&~mask)|(value&mask);
}
static void hrtimer_cancel(int *timer) {(void)timer;}
static void synchronize_irq(int irq) {
    (void)irq;pthread_mutex_lock(&gate);masked=true;pthread_cond_broadcast(&event);pthread_mutex_unlock(&gate);
}
static void napi_disable(struct napi_struct *napi) {
    (void)napi;pthread_mutex_lock(&gate);while(owned)pthread_cond_wait(&event,&gate);pthread_mutex_unlock(&gate);
}
static void rtl_qm_rx_stop(struct rtl_qm_rx *qm) {qm->stopped=true;}
static void rtl_dma_tx_stop(int *dma) {*dma=0;}
static int rtl_qm_rx_poll(struct rtl_qm_rx *qm,struct napi_struct *napi,int budget) {
    (void)qm;(void)napi;(void)budget;
    if(scenario==2) {
        pthread_mutex_lock(&gate);window=true;pthread_cond_broadcast(&event);
        while(!masked)pthread_cond_wait(&event,&gate);pthread_mutex_unlock(&gate);
    }
    return 0;
}
static int napi_complete_done(struct napi_struct *napi,int count) {
    (void)napi;(void)count;
    pthread_mutex_lock(&gate);owned=false;window=true;pthread_cond_broadcast(&event);
    if(current->rx_lock.held) {while(!attempted)pthread_cond_wait(&event,&gate);}
    else {while(!finished)pthread_cond_wait(&event,&gate);}
    pthread_mutex_unlock(&gate);return 1;
}
'''
test += stop + '\nstatic int poll_completion(struct rtl_xram *priv,int budget) {\nstruct napi_struct *napi=&priv->napi;int packets=0;\n' + branch + '\nreturn packets;\n}\n'
test += r'''
static void *close_engine(void *arg) {
    pthread_mutex_lock(&gate);while(!window)pthread_cond_wait(&event,&gate);pthread_mutex_unlock(&gate);
    rtl_engine_stop(arg);
    pthread_mutex_lock(&gate);finished=true;pthread_cond_broadcast(&event);pthread_mutex_unlock(&gate);return NULL;
}
int main(void) {
    for(scenario=1;scenario<=2;scenario++) {
        struct rtl_xram priv={.qm_rx=true,.dma_tx=true,.rx_active=true,.mask=255,.dma=1};
        current=&priv;owned=true;window=attempted=masked=finished=false;
        pthread_mutex_init(&priv.rx_lock.mutex,NULL);
        assert(!pthread_create(&closer,NULL,close_engine,&priv));
        assert(poll_completion(&priv,64)==0);assert(!pthread_join(closer,NULL));
        assert(priv.qm.stopped && priv.mask==0 && !priv.rx_active && !priv.dma);
        pthread_mutex_destroy(&priv.rx_lock.mutex);
    }
    puts("Actual NI stop/poll branch: NAPI completion before and after IRQ masking both leave QM stopped and RX IRQ disabled");
}
'''
with tempfile.TemporaryDirectory(prefix='rx-stop-test-', dir=root / 'artifacts') as directory:
    directory = Path(directory)
    cfile = directory / 'rx-stop.c'
    binary = directory / 'rx-stop'
    cfile.write_text(test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function', '-Wno-misleading-indentation',
                    '-O1', '-g', '-pthread', '-fsanitize=address,undefined', str(cfile), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
    if result.returncode:
        print(result.stderr,end='')
        raise SystemExit(result.returncode)
    print(result.stdout,end='')
if not args.revision:
    (root / 'logs/rx-shutdown-test.json').write_text(json.dumps({'actual_stop_and_poll_branch':True,
        'controlled_threads':2,'both_interleavings_passed':True,'hardware_access':False},indent=2)+'\n')
