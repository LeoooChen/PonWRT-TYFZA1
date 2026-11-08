#!/usr/bin/env python3
"""Execute native callback lifetime through real concurrent RCU-like readers."""
from pathlib import Path
import re
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
INCLUDE=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/include'
source=(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/net/core/rtl9617c-bridge.c').read_text()
code=r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#include "linux/rtl9617c-bridge.h"
#define DEFINE_SPINLOCK(v) pthread_mutex_t v=PTHREAD_MUTEX_INITIALIZER
#define spin_lock_irqsave(p,f) do{(f)=0;assert(!pthread_mutex_lock(p));}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(f);assert(!pthread_mutex_unlock(p));}while(0)
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define cpu_to_le32(v) (v)
#define dma_wmb() atomic_thread_fence(memory_order_seq_cst)
#define smp_rmb() atomic_thread_fence(memory_order_seq_cst)
#define __rcu
#define EXPORT_SYMBOL_GPL(v)
#define DEFINE_MUTEX(v) pthread_mutex_t v=PTHREAD_MUTEX_INITIALIZER
#define mutex_lock(p) assert(!pthread_mutex_lock(p))
#define mutex_unlock(p) assert(!pthread_mutex_unlock(p))
typedef _Atomic u64 atomic64_t;
#define ATOMIC64_INIT(v) (v)
static u64 atomic64_inc_return(atomic64_t *p){return atomic_fetch_add(p,1)+1;}
static void atomic64_inc(atomic64_t *p){atomic_fetch_add(p,1);}
static u64 atomic64_read(atomic64_t *p){return atomic_load(p);}
#define rcu_assign_pointer(p,v) __atomic_store_n(&(p),(v),__ATOMIC_SEQ_CST)
#define RCU_INIT_POINTER(p,v) rcu_assign_pointer(p,v)
#define rcu_access_pointer(p) __atomic_load_n(&(p),__ATOMIC_SEQ_CST)
#define rcu_dereference(p) rcu_access_pointer(p)
static pthread_rwlock_t readers=PTHREAD_RWLOCK_INITIALIZER;
static pthread_mutex_t sync_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t sync_cond=PTHREAD_COND_INITIALIZER;
static unsigned sync_count;
static void rcu_read_lock(void){assert(!pthread_rwlock_rdlock(&readers));}
static void rcu_read_unlock(void){assert(!pthread_rwlock_unlock(&readers));}
static void synchronize_net(void){
 mutex_lock(&sync_lock);sync_count++;pthread_cond_broadcast(&sync_cond);mutex_unlock(&sync_lock);
 assert(!pthread_rwlock_wrlock(&readers));assert(!pthread_rwlock_unlock(&readers));
}
static void wait_sync(unsigned n){
 mutex_lock(&sync_lock);while(sync_count<n)assert(!pthread_cond_wait(&sync_cond,&sync_lock));mutex_unlock(&sync_lock);
}
'''
code+=r'''
#define SKB_EXT_RTL9617C_BRIDGE 0
struct net_device {int unused;};struct sk_buff {u64 ticket;struct rtl9617c_bridge_frame saved;};
static void *skb_ext_find(const struct sk_buff *s,int id){assert(!id);return (void *)&s->saved;}
static void skb_ext_copy(struct sk_buff *to,const struct sk_buff *from){to->saved=from->saved;}
static void *skb_ext_add(struct sk_buff *s,int id){return skb_ext_find(s,id);}
'''
code+=re.sub(r'^#include .*\n','',source,flags=re.M)
code+=r'''

struct owner {atomic_uint writers,selected,outputs,completed;u64 pending;};
static void change_begin(void *context,struct net_device *dev){(void)dev;atomic_fetch_add(&((struct owner *)context)->writers,1);}
static void change_end(void *context){assert(atomic_fetch_sub(&((struct owner *)context)->writers,1));}
static u64 capture_begin(void *context,struct sk_buff *skb,bool local,u64 ticket){
 struct owner *o=context;(void)local;if(atomic_load(&o->writers))return 0;
 o->pending=skb->ticket=ticket;return ticket;
}
static void select_packet(void *context,struct sk_buff *skb,struct net_device *dev){
 struct owner *o=context;(void)dev;if(skb->ticket==o->pending)o->selected++;
}
static void select_address(void *context,struct sk_buff *skb,struct net_device *dev,const u8 *address){assert(address[0]==2);select_packet(context,skb,dev);}
static void output_packet(void *context,struct sk_buff *skb){struct owner *o=context;if(skb->ticket==o->pending)o->outputs++;}
static void complete_packet(void *context,u64 ticket,bool complete){struct owner *o=context;if(ticket==o->pending&&complete)o->completed++;}
static int host_local(void *ctx,struct sk_buff *s,struct net_device *d,u16 vid){(void)ctx;(void)s;(void)d;return vid?-ESTALE:1;}
static int host_receive(void *ctx,struct sk_buff *s,unsigned group,unsigned source){(void)ctx;s->saved.multicast_host_group=group;return source?-ESTALE:0;}
static bool race_stamp;
static bool host_tx_valid(void *ctx,const struct sk_buff *skb){
 if(race_stamp){race_stamp=false;rtl9617c_bridge_mcast_invalidate();}
 struct owner *o=ctx;return !atomic_load(&o->writers) && skb->saved.multicast_host_owner==o->pending;
}
static const struct rtl9617c_bridge_mcast_ops *thread_ops;
static void *register_owner(void *unused){(void)unused;assert(!rtl9617c_bridge_mcast_register(thread_ops));return NULL;}
static void *unregister_owner(void *unused){(void)unused;rtl9617c_bridge_mcast_unregister(thread_ops);return NULL;}
int main(void){
 struct owner first={0},second={0};pthread_t thread;
 struct rtl9617c_bridge_mcast_ops a={&first,change_begin,change_end,capture_begin,select_packet,select_address,output_packet,complete_packet,host_receive,host_local,host_tx_valid},b=a;
 b.context=&second;struct sk_buff old={0},fresh={0};struct net_device dev;
 const struct rtl9617c_bridge_mcast_ops *token=rtl9617c_bridge_mcast_begin(&dev);assert(!token);
 thread_ops=&a;assert(!pthread_create(&thread,NULL,register_owner,NULL));wait_sync(1);
 /* Registration published, but the writer begun without an owner still lives. */
 assert(atomic_load(&first.writers)==1&&rcu_access_pointer(rtl_bridge_mcast)==&a);
 rtl9617c_bridge_mcast_end(token);assert(!pthread_join(thread,NULL));assert(!first.writers);
 assert(rtl9617c_bridge_mcast_register(&b)==-EBUSY);
 u64 policy=rtl9617c_bridge_wifi_policy_generation();
 token=rtl9617c_bridge_wifi_policy_begin(&dev);
 assert(first.writers==1&&rtl9617c_bridge_wifi_policy_generation()==policy+1);
 rtl9617c_bridge_mcast_end(token);
 u64 old_ticket=rtl9617c_bridge_mcast_capture(&old,false);assert(old_ticket);
 rtl9617c_bridge_mcast_select(&old,&dev);rtl9617c_bridge_mcast_output(&old);
 rtl9617c_bridge_mcast_complete(old_ticket,true);assert(first.completed==1&&first.outputs==1&&first.selected==1);
 token=rtl9617c_bridge_mcast_begin(&dev);assert(token==&a&&first.writers==1);
 assert(!rtl9617c_bridge_mcast_capture(&fresh,false));
 assert(!pthread_create(&thread,NULL,unregister_owner,NULL));wait_sync(2);
 assert(first.writers==2&&!rcu_access_pointer(rtl_bridge_mcast));
 rtl9617c_bridge_mcast_end(token);assert(!pthread_join(thread,NULL));assert(!first.writers);
 assert(!rtl9617c_bridge_mcast_register(&b));
 u64 new_ticket=rtl9617c_bridge_mcast_capture(&fresh,false);assert(new_ticket>old_ticket);
 rtl9617c_bridge_mcast_select(&old,&dev);rtl9617c_bridge_mcast_output(&old);rtl9617c_bridge_mcast_complete(old_ticket,true);
 assert(!second.selected&&!second.outputs&&!second.completed);
 const u8 address[6]={2,1,2,3,4,5};rtl9617c_bridge_mcast_select_addr(&old,&dev,address);assert(!second.selected);
 rtl9617c_bridge_mcast_select(&fresh,&dev);rtl9617c_bridge_mcast_output(&fresh);rtl9617c_bridge_mcast_complete(new_ticket,true);
 assert(second.selected==1&&second.outputs==1&&second.completed==1);
 rtl9617c_bridge_mcast_select_addr(&fresh,&dev,address);assert(second.selected==2);
 assert(!rtl9617c_bridge_mcast_receive(&fresh,7,0));
 assert(rtl9617c_bridge_mcast_local(&fresh,&dev,1)==-ESTALE&&fresh.saved.multicast_host_group==7);
 assert(rtl9617c_bridge_mcast_local(&fresh,&dev,0)==1&&!fresh.saved.multicast_host_group);
 assert(rtl9617c_bridge_mcast_local(&fresh,&dev,0)==0);
 fresh.saved.multicast_direct=true;fresh.saved.multicast_host_owner=second.pending;
 fresh.saved.multicast_host_group=17;fresh.saved.multicast_epoch=9;
 assert(!rtl9617c_bridge_mcast_stale(&fresh));
 unsigned int mirrors[3]={};u32 stamp;
 assert(!rtl9617c_bridge_mcast_pe_register(&mirrors[0]));
 assert(!rtl9617c_bridge_mcast_pe_register(&mirrors[1]));
 assert(rtl9617c_bridge_mcast_pe_register(&mirrors[0])==-EEXIST);
 assert(rtl9617c_bridge_mcast_pe_register(&mirrors[2])==-ENOSPC);
 assert(!rtl9617c_bridge_mcast_tx_stamp(&fresh,&stamp) && stamp && stamp==mirrors[0] && stamp==mirrors[1]);
 race_stamp=true;assert(rtl9617c_bridge_mcast_tx_stamp(&fresh,&stamp)==-ESTALE && !stamp);
 assert(!rtl9617c_bridge_mcast_tx_stamp(&fresh,&stamp) && stamp==mirrors[0]);
 rtl9617c_bridge_mcast_pe_unregister(&mirrors[0]);assert(!mirrors[0]);
 rtl_mcast_cpu_generation=UINT32_MAX;rtl9617c_bridge_mcast_invalidate();
 assert(!mirrors[1] && rtl9617c_bridge_mcast_tx_stamp(&fresh,&stamp)==-ESTALE);
 rtl9617c_bridge_mcast_invalidate();assert(!rtl_mcast_cpu_generation);
 rtl9617c_bridge_mcast_pe_unregister(&mirrors[1]);
 rtl_mcast_cpu_generation=1;
 struct sk_buff copy={0};assert(rtl9617c_bridge_mcast_differs(&copy,&fresh));
 rtl9617c_bridge_mcast_copy(&copy,&fresh);assert(!rtl9617c_bridge_mcast_differs(&copy,&fresh));
 copy.saved.multicast_host_group++;assert(rtl9617c_bridge_mcast_differs(&copy,&fresh));
 copy.saved=fresh.saved;copy.saved.multicast_host_owner--;assert(rtl9617c_bridge_mcast_stale(&copy));
 token=rtl9617c_bridge_mcast_begin(&dev);assert(rtl9617c_bridge_mcast_stale(&fresh));rtl9617c_bridge_mcast_end(token);
 rtl9617c_bridge_mcast_unregister(&a);assert(rcu_access_pointer(rtl_bridge_mcast)==&b);
 rtl9617c_bridge_mcast_unregister(&b);assert(!second.writers&&!rcu_access_pointer(rtl_bridge_mcast));
 assert(rtl9617c_bridge_mcast_stale(&fresh));
 assert(!rtl9617c_bridge_mcast_register(&a));assert(rtl9617c_bridge_mcast_stale(&fresh));rtl9617c_bridge_mcast_unregister(&a);
 puts("Native multicast core: pre-registration writers, owner exclusion, concurrent detach, delayed old skb and unique tickets passed");
}
'''
with tempfile.TemporaryDirectory(prefix='bridge-mcast-core-') as name:
 p=Path(name);(p/'test.c').write_text(code)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-I'+str(INCLUDE),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,timeout=15)
