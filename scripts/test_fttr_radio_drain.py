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
#define ATOMIC64_INIT(v) (v)
#define ATOMIC_INIT(v) (v)
#define EXPORT_SYMBOL_GPL(v)
#define cpu_to_le32(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define DEFINE_SPINLOCK(v) pthread_mutex_t v=PTHREAD_MUTEX_INITIALIZER
#define spin_lock_irqsave(p,f) ((f)=0,assert(!pthread_mutex_lock(p)))
#define spin_unlock_irqrestore(p,f) ((void)(f),assert(!pthread_mutex_unlock(p)))
#define atomic_read(p) atomic_load(p)
#define atomic_inc(p) atomic_fetch_add(p,1)
#define atomic_dec_and_test(p) (atomic_fetch_sub(p,1)==1)
#define atomic64_read_acquire(p) atomic_load_explicit(p,memory_order_acquire)
#define atomic64_set_release(p,v) atomic_store_explicit(p,v,memory_order_release)
#define dma_wmb() atomic_thread_fence(memory_order_release)
static _Thread_local bool rtnl;
#define ASSERT_RTNL() assert(rtnl)
struct rtl_fttr_pe_generation {u32 low,high;};
u64 rtl9617c_fttr_generation_read(void);
struct test_wait {pthread_mutex_t lock;pthread_cond_t cond;};
#define DECLARE_WAIT_QUEUE_HEAD(v) struct test_wait v={PTHREAD_MUTEX_INITIALIZER,PTHREAD_COND_INITIALIZER}
static atomic_bool waiting;
static void wake_up_all(struct test_wait *w){
 assert(!pthread_mutex_lock(&w->lock));assert(!pthread_cond_broadcast(&w->cond));assert(!pthread_mutex_unlock(&w->lock));
}
#define msecs_to_jiffies(v) (v)
/* Preserve wait queue acquire/check/wake ordering, shortening only the
 * modeled timeout for offline missing-RPQ scenarios. */
#define wait_event_timeout(w,condition,timeout) ({ \
 assert((timeout)==1000);int rc=0;struct timespec until;clock_gettime(CLOCK_REALTIME,&until); \
 until.tv_nsec+=100000000;if(until.tv_nsec>=1000000000){until.tv_nsec-=1000000000;until.tv_sec++;} \
 pthread_mutex_lock(&(w).lock);atomic_store(&waiting,true); \
 while(!(condition)&&!rc)rc=pthread_cond_timedwait(&(w).cond,&(w).lock,&until); \
 long result=(condition)?1:0;pthread_mutex_unlock(&(w).lock);result;})
''' + source + r'''
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
 rtnl=true;pthread_t a,b;struct rtl_fttr_pe_generation mirror[2]={};
 assert(!rtl9617c_fttr_pe_register(&mirror[0])&&!rtl9617c_fttr_pe_register(&mirror[1]));
 for(unsigned n=0;n<128;n++) {
  atomic_store(&waiting,false);atomic_store(&released,0);
  assert(!rtl9617c_fttr_generation_set(true));stamp=rtl9617c_fttr_generation_read();
  pthread_barrier_init(&start,NULL,3);pthread_create(&a,NULL,complete,NULL);pthread_create(&b,NULL,complete,NULL);
  pthread_barrier_wait(&start);assert(atomic_read(&rtl_fttr_tx_pending)==2);
  assert(!rtl9617c_fttr_generation_set(false));
  assert(mirror[0].low==(u32)(stamp+1)&&mirror[1].low==(u32)(stamp+1));
  assert(!rtl9617c_fttr_tx_get(stamp)&&rtl9617c_fttr_generation_set(true)==-EBUSY);
  assert(!rtl9617c_fttr_tx_drain()&&atomic_load(&released)==2);
  pthread_join(a,NULL);pthread_join(b,NULL);pthread_barrier_destroy(&start);
 }
 for(unsigned n=0;n<128;n++) {
  assert(!rtl9617c_fttr_generation_set(true));stamp=rtl9617c_fttr_generation_read();bool accepted=false;
  pthread_barrier_init(&start,NULL,2);pthread_create(&a,NULL,race_sender,&accepted);pthread_barrier_wait(&start);
  assert(!rtl9617c_fttr_generation_set(false));pthread_join(a,NULL);
  assert(atomic_read(&rtl_fttr_tx_pending)==accepted);
  if(accepted)rtl9617c_fttr_tx_put(stamp);
  assert(!rtl9617c_fttr_tx_drain());pthread_barrier_destroy(&start);
 }
 assert(!rtl9617c_fttr_generation_set(true));stamp=rtl9617c_fttr_generation_read();assert(rtl9617c_fttr_tx_get(stamp));
 assert(!rtl9617c_fttr_generation_set(false));
 rtl9617c_fttr_pe_unregister(&mirror[0]);rtl9617c_fttr_pe_unregister(&mirror[1]);
 assert(rtl9617c_fttr_tx_drain()==-ETIMEDOUT&&rtl9617c_fttr_generation_set(true)==-EBUSY);
 rtl9617c_fttr_tx_put(stamp);assert(!rtl9617c_fttr_tx_drain()&&!rtl9617c_fttr_generation_set(true));
 puts("FTTR radio drain:256 concurrent dual-completion/close-admission races; missing completion survives radio unregister; timeout stays closed and late completion permits retry");
}
'''
with tempfile.TemporaryDirectory(prefix='fttr-radio-drain-') as tmp:
    path = Path(tmp)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-pthread',
                    '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
