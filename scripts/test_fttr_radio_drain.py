#!/usr/bin/env python3
"""Run production FTTR admission/drain with concurrent radio completions."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/net/core/rtl9617c-fttr.c').read_text()
source = re.sub(r'^#include .*\n', '', source.split('bool rtl9617c_fttr_stamp(', 1)[0], flags=re.M)
code = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <time.h>
#include <sched.h>
typedef uint64_t u64;typedef uint32_t u32;
typedef _Atomic u64 atomic64_t;typedef atomic_uint atomic_t;
#define U64_MAX UINT64_MAX
#define U32_MAX UINT32_MAX
#define le32_to_cpu(v) (v)
#define READ_ONCE(v) (v)
#define dma_rmb() atomic_thread_fence(memory_order_acquire)
#define ATOMIC64_INIT(v) (v)
#define ATOMIC_INIT(v) (v)
#define EXPORT_SYMBOL_GPL(v)
#define cpu_to_le32(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define DEFINE_SPINLOCK(v) pthread_mutex_t v=PTHREAD_MUTEX_INITIALIZER
#define spin_lock_irqsave(p,f) ((f)=0,assert(!pthread_mutex_lock(p)))
#define spin_unlock_irqrestore(p,f) ((void)(f),assert(!pthread_mutex_unlock(p)))
#define atomic_read(p) atomic_load(p)
#define atomic_read_acquire(p) atomic_load_explicit(p,memory_order_acquire)
#define atomic_inc(p) atomic_fetch_add(p,1)
static unsigned atomic_dec_return_release(atomic_t *p){return atomic_fetch_sub_explicit(p,1,memory_order_release)-1;}
#define atomic64_read_acquire(p) atomic_load_explicit(p,memory_order_acquire)
#define atomic64_set_release(p,v) atomic_store_explicit(p,v,memory_order_release)
#define dma_wmb() atomic_thread_fence(memory_order_release)
static _Thread_local bool rtnl;
#define ASSERT_RTNL() assert(rtnl)
''' + (root / 'port/overlay/target/linux/rtl9617c/files-6.18/include/linux/rtl9617c-fttr-pe.h').read_text() + r'''

u64 rtl9617c_fttr_generation_read(void);
static atomic_bool waiting;
static unsigned long test_jiffies(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000UL+t.tv_nsec/1000000;}
#define jiffies test_jiffies()
#define time_after_eq(a,b) ((long)((a)-(b))>=0)
#define msecs_to_jiffies(v) ((v)/10)
static void usleep_range(unsigned a,unsigned b);
''' + source + r'''
static void usleep_range(unsigned a,unsigned b){
 assert(a==1000&&b==2000);atomic_store(&waiting,true);
 pthread_mutex_lock(&rtl_fttr_pe_lock);
 for(unsigned i=0;i<2;i++)if(rtl_fttr_pe_drains[i])rtl_fttr_pe_drains[i]->ack=rtl_fttr_pe_drains[i]->request;
 pthread_mutex_unlock(&rtl_fttr_pe_lock);
 struct timespec t={.tv_nsec=1000000};nanosleep(&t,NULL);
}
static int open_generation(void){int ret=rtl9617c_fttr_generation_read()&1?rtl9617c_fttr_tx_drain():0;return ret?ret:rtl9617c_fttr_generation_set(true);}
static pthread_barrier_t start;
static u64 stamp;
static atomic_uint released;
static void *complete(void *arg){
 (void)arg;assert(rtl9617c_fttr_tx_get(stamp));
 pthread_barrier_wait(&start);
 while(!atomic_load(&waiting))sched_yield();
 /* The DMA unmap and skb disposition happen before relinquishing the
  * accepted reference; the real driver sequence is tested separately. */
 atomic_fetch_add(&released,1);rtl9617c_fttr_tx_put(stamp);return NULL;
}
static void *race_sender(void *arg){
 bool *accepted=arg;pthread_barrier_wait(&start);
 *accepted=rtl9617c_fttr_tx_get(stamp);return NULL;
}
int main(void){
 rtnl=true;pthread_t a,b;struct rtl_fttr_pe_generation mirror[2]={};struct rtl_fttr_pe_drain drains[2]={};
 assert(!rtl9617c_fttr_pe_register(&mirror[0],&drains[0])&&!rtl9617c_fttr_pe_register(&mirror[1],&drains[1]));
 for(unsigned n=0;n<128;n++) {
  atomic_store(&waiting,false);atomic_store(&released,0);
  assert(!open_generation());stamp=rtl9617c_fttr_generation_read();atomic_store(&waiting,false);
  pthread_barrier_init(&start,NULL,3);pthread_create(&a,NULL,complete,NULL);pthread_create(&b,NULL,complete,NULL);
  pthread_barrier_wait(&start);assert(atomic_read(&rtl_fttr_tx_pending)==2);
  assert(!rtl9617c_fttr_generation_set(false));
  assert(mirror[0].low==(u32)(stamp+1)&&mirror[1].low==(u32)(stamp+1));
  assert(!rtl9617c_fttr_tx_get(stamp)&&rtl9617c_fttr_generation_set(true)==-EBUSY);
  assert(!rtl9617c_fttr_tx_drain()&&atomic_load(&released)==2);
  pthread_join(a,NULL);pthread_join(b,NULL);pthread_barrier_destroy(&start);
 }
 for(unsigned n=0;n<128;n++) {
  assert(!open_generation());stamp=rtl9617c_fttr_generation_read();bool accepted=false;
  pthread_barrier_init(&start,NULL,2);pthread_create(&a,NULL,race_sender,&accepted);pthread_barrier_wait(&start);
  assert(!rtl9617c_fttr_generation_set(false));pthread_join(a,NULL);
  assert(atomic_read(&rtl_fttr_tx_pending)==accepted);
  if(accepted)rtl9617c_fttr_tx_put(stamp);
  assert(!rtl9617c_fttr_tx_drain());pthread_barrier_destroy(&start);
 }
 assert(!open_generation());stamp=rtl9617c_fttr_generation_read();assert(rtl9617c_fttr_tx_get(stamp));
 assert(!rtl9617c_fttr_generation_set(false));
 rtl9617c_fttr_pe_unregister(&mirror[0]);rtl9617c_fttr_pe_unregister(&mirror[1]);
 assert(rtl9617c_fttr_tx_drain()==-ETIMEDOUT&&rtl9617c_fttr_generation_set(true)==-EBUSY);
 rtl9617c_fttr_tx_put(stamp);assert(!rtl9617c_fttr_tx_drain()&&!open_generation());
 puts("FTTR radio drain:256 concurrent dual-completion/close-admission races; missing completion survives radio unregister; timeout stays closed and late completion permits retry");
}
'''
with tempfile.TemporaryDirectory(prefix='fttr-radio-drain-') as tmp:
    path = Path(tmp)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-pthread',
                    '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
