#!/usr/bin/env python3
"""Execute overlapping native station/key waits and their notification ownership."""
from pathlib import Path
import argparse
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--before', action='store_true')
args = parser.parse_args()
p = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c'
source = p.read_text()
if args.before:
    source = subprocess.check_output(['git', 'show', 'efc3789:' + str(p.relative_to(root))], cwd=root, text=True)


def function(name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    at = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[at] == '{') - (source[at] == '}')
        at += 1
    return source[start:at] + '\n'


common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_TX_ENTRIES 256
#define IEEE80211_NUM_TIDS 16
#define spin_lock_irqsave(p,f) ((f)=0,pthread_mutex_lock(p))
#define spin_unlock_irqrestore(p,f) ((void)(f),pthread_mutex_unlock(p))
#define for_each_set_bit(i,p,n) for(i=0;i<n;i++)if(p[i])
#define dma_wmb() ((void)0)
#define msecs_to_jiffies(n) (n)
struct wait_queue {pthread_mutex_t lock;pthread_cond_t condition;};
struct rtl_ax_tx {bool pe_active;pthread_mutex_t lock;struct wait_queue key_flush_wait;struct {void *memory;}pe;
 bool pending[256];struct {unsigned macid,tid,key_index;bool encrypted;} slots[256];};
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
#define wait_event_timeout(q,predicate,ms) ({ \
 struct timespec deadline;clock_gettime(CLOCK_REALTIME,&deadline);deadline.tv_sec+=(ms)/1000; \
 int status=0;long result=1;pthread_mutex_lock(&(q).lock); \
 while(!(predicate)&&!status)status=pthread_cond_timedwait(&(q).condition,&(q).lock,&deadline); \
 if(!(predicate))result=0;pthread_mutex_unlock(&(q).lock);result;})
'''
case = r'''
struct args {struct rtl_ax_tx *tx;bool key;int result;};
static void *waiter(void *p){struct args *a=p;a->result=a->key?rtl_ax_tx_flush_key(a->tx,43):rtl_ax_tx_wait_station(a->tx,7,0);return 0;}
static u32 count(struct rtl_ax_tx *tx){pthread_mutex_lock(&tx->lock);u32 n=get_unaligned_le32(tx->pe.memory+0x2138);pthread_mutex_unlock(&tx->lock);return n;}
int main(void){
 unsigned char memory[0x20000]={};struct rtl_ax_tx tx={.pe_active=true,.lock=PTHREAD_MUTEX_INITIALIZER,
 .key_flush_wait={PTHREAD_MUTEX_INITIALIZER,PTHREAD_COND_INITIALIZER},.pe={memory}};
 put_unaligned_le32(0x6000000,memory+0xc400);put_unaligned_le32(7<<8|42<<17,memory+0xc404);
 put_unaligned_le32(0x6001000,memory+0xc408);put_unaligned_le32(8<<8|43<<17,memory+0xc40c);
 put_unaligned_le32(1,memory+0x1101c);
 struct args a={&tx,false,-1},b={&tx,true,-1};pthread_t t1,t2;
 assert(!pthread_create(&t1,0,waiter,&a));assert(!pthread_create(&t2,0,waiter,&b));
 unsigned armed=0;
 for(unsigned i=0;i<500;i++){
  armed=count(&tx);
#ifdef EXPECT_OLD
  if(armed==1){usleep(10000);break;}
#else
  if(armed==2)break;
#endif
  usleep(1000);
 }
#ifdef EXPECT_OLD
 assert(armed==1);
#else
 assert(armed==2);
#endif
 /* Device owner updates and explicit wake use one modeled waitqueue lock. */
 pthread_mutex_lock(&tx.key_flush_wait.lock);put_unaligned_le32(BIT(31)|7<<8|42<<17,memory+0xc404);
 pthread_cond_broadcast(&tx.key_flush_wait.condition);pthread_mutex_unlock(&tx.key_flush_wait.lock);
 assert(!pthread_join(t1,0)&&!a.result);unsigned left=count(&tx);assert(left==1);
 pthread_mutex_lock(&tx.key_flush_wait.lock);put_unaligned_le32(BIT(31)|8<<8|43<<17,memory+0xc40c);
 pthread_cond_broadcast(&tx.key_flush_wait.condition);pthread_mutex_unlock(&tx.key_flush_wait.lock);
 assert(!pthread_join(t2,0)&&!b.result);unsigned after=count(&tx);
#ifdef EXPECT_OLD
 assert(after==1);
#else
 assert(after==0);
 /* Timeout must release the host's subscription, too. */
 put_unaligned_le32(8<<8|43<<17,memory+0xc40c);
 assert(rtl_ax_tx_flush_key(&tx,43)==-ETIMEDOUT&&count(&tx)==0);
 tx.pe_active=false;put_unaligned_le32(0,memory+0xc408);
 assert(!rtl_ax_tx_flush_key(&tx,43)&&count(&tx)==0);
#endif
 printf("{\"armed_waiters\":%u,\"after_first_waiter\":%u,\"after_both_waiters\":%u}\n",armed,left,after);
}
'''
names = ['rtl_ax_tx_pe_pending', 'rtl_ax_tx_key_pending', 'rtl_ax_tx_station_pending']
if not args.before:
    names += ['rtl_ax_tx_waiters']
names += ['rtl_ax_tx_flush_key', 'rtl_ax_tx_wait_station']
with tempfile.TemporaryDirectory(prefix='pe-waiters-', dir=root / 'artifacts') as temp:
    p = Path(temp)
    (p / 'case.c').write_text(common + ''.join(function(n) for n in names) + case)
    cmd = ['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-misleading-indentation', '-pthread', '-fsanitize=address,undefined']
    if args.before:
        cmd += ['-DEXPECT_OLD']
    subprocess.run(cmd + [str(p / 'case.c'), '-o', str(p / 'case')], check=True)
    subprocess.run([str(p / 'case')], check=True)
