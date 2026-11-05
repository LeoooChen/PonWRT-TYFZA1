#!/usr/bin/env python3
"""Exercise actual flowtable GC/work functions with deterministic interleavings.

Workqueues, RCU unlink/free and device stats are modeled, not hardware timing.
--before reverses the fix in a temporary copy and asserts the original bugs.
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--kernel", type=Path, required=True)
parser.add_argument("--before", action="store_true")
args = parser.parse_args()


def function(text, name):
    match = re.search(r"^.*\b" + name + r"\([^;]*?\)\s*\{", text, re.M)
    start = match.start()
    if text[start:].startswith(name + "("):
        start = text.rfind("\n", 0, start - 1) + 1
    end = text.index("{", match.start()) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + "\n"


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
typedef int32_t __s32; typedef uint64_t u64;
enum {NF_FLOW_CLOSING,NF_FLOW_TEARDOWN,NF_FLOW_HW,NF_FLOW_HW_DYING,
 NF_FLOW_HW_DEAD,NF_FLOW_HW_PENDING,NF_FLOW_HW_BIDIRECTIONAL};
enum {FLOW_CLS_REPLACE,FLOW_CLS_DESTROY,FLOW_CLS_STATS};
#define BIT(n) (1UL<<(n))
#define GFP_ATOMIC 0
#define NF_FLOWTABLE_COUNTER 1
#define FLOW_OFFLOAD_DIR_MAX 2
enum flow_offload_tuple_dir {FLOW_OFFLOAD_DIR_ORIGINAL,FLOW_OFFLOAD_DIR_REPLY};
#define max_t(t,a,b) ((t)(a)>(t)(b)?(t)(a):(t)(b))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define INIT_WORK(p,cb) ((p)->func=(cb))
#define NF_FLOW_TABLE_STAT_DEC_ATOMIC(n,c) ((void)(n))
#define WARN_ON_ONCE(v) assert(!(v))
struct net {int unused;}; struct nf_conn {bool dying;};
struct flow_offload {_Atomic unsigned long flags;unsigned timeout;struct nf_conn *ct;};
struct nf_flowtable {unsigned flags;struct net *net;};
struct work_struct {void (*func)(struct work_struct *);};
struct flow_offload_work {unsigned cmd;struct nf_flowtable *flowtable;
 struct flow_offload *flow;struct work_struct work;};
struct flow_stats {u64 pkts,bytes,lastused;};
static unsigned now,unlinked,unsafe_unlink,adds,deletes,stats,accounted;
static bool fail_alloc,bridge_changed,late_pending,late_dying,worker_gc;
static struct flow_offload_work *pending,*running;
static struct nf_flowtable *table;
static void nf_flow_offload_gc_step(struct nf_flowtable *,struct flow_offload *,void *);
static struct net *read_pnet(struct net **n){return *n;}
static bool test_bit(unsigned n,const _Atomic unsigned long *p){
 bool value=!!(atomic_load(p)&BIT(n));
 /* A stale RCU submitter acquires PENDING after GC's first check. */
 if(n==NF_FLOW_HW_DEAD&&value&&late_pending){
  late_pending=false;atomic_fetch_or((_Atomic unsigned long *)p,BIT(NF_FLOW_HW_PENDING));
 }
 return value;
}
static bool test_bit_acquire(unsigned n,const _Atomic unsigned long *p){
 return !!(atomic_load_explicit(p,memory_order_acquire)&BIT(n));
}
static bool test_and_set_bit(unsigned n,_Atomic unsigned long *p){
 /* DESTROY finished after the caller decided to submit new work. */
 if(n==NF_FLOW_HW_PENDING&&late_dying){
  late_dying=false;atomic_fetch_or(p,BIT(NF_FLOW_HW_DYING)|BIT(NF_FLOW_HW_DEAD));
 }
 return !!(atomic_fetch_or(p,BIT(n))&BIT(n));
}
static void set_bit(unsigned n,_Atomic unsigned long *p){atomic_fetch_or(p,BIT(n));}
static void clear_bit(unsigned n,_Atomic unsigned long *p){
 atomic_fetch_and(p,~BIT(n));
 if(n==NF_FLOW_HW_PENDING&&worker_gc&&running){
  worker_gc=false;nf_flow_offload_gc_step(table,running->flow,NULL);
  assert(!unlinked); /* DESTROY still has its final DEAD store to perform. */
 }
}
static void smp_mb__before_atomic(void){atomic_thread_fence(memory_order_seq_cst);}
static void *kmalloc(size_t n,int flags){(void)flags;return fail_alloc?NULL:calloc(1,n);}
static void kfree(void *p){free(p);}
static unsigned flow_offload_get_timeout(struct flow_offload *f){assert(f->ct);return 3000;}
static int nf_flow_timeout_delta(unsigned t){return (__s32)(t-now);}
static bool nf_flow_has_expired(struct flow_offload *f){return nf_flow_timeout_delta(f->timeout)<=0;}
static bool nf_ct_is_dying(struct nf_conn *ct){return ct->dying;}
static bool nf_flow_bridge_changed(struct flow_offload *f){(void)f;return bridge_changed;}
static bool nf_flow_custom_gc(struct nf_flowtable *t,struct flow_offload *f){(void)t;(void)f;return false;}
static void flow_offload_teardown(struct flow_offload *f){set_bit(NF_FLOW_TEARDOWN,&f->flags);}
static void nf_flow_table_extend_ct_timeout(struct nf_conn *ct){(void)ct;}
static void flow_offload_work_add(struct flow_offload_work *w){(void)w;adds++;}
static void flow_offload_work_del(struct flow_offload_work *w){(void)w;deletes++;}
static void flow_offload_tuple_stats(struct flow_offload_work *w,unsigned d,struct flow_stats *s){
 (void)w;(void)d;stats++;s->lastused=now;s->pkts=1;s->bytes=100;
}
static void flow_offload_refresh_bridge(struct flow_offload *f,unsigned d,u64 t){(void)f;(void)d;(void)t;}
static void nf_ct_acct_add(struct nf_conn *c,unsigned d,u64 p,u64 b){
 (void)c;(void)d;assert(p==1&&b==100);accounted++;
}
static void flow_offload_queue_work(struct flow_offload_work *w){assert(!pending);pending=w;}
static void flow_offload_del(struct nf_flowtable *t,struct flow_offload *f){
 (void)t;(void)f;unlinked++;
 /* GC's retirement PENDING bit is not itself a queued task. */
 if(pending||running)unsafe_unlink++;
}
'''

cases = r'''
static void reset(struct flow_offload *f,unsigned long flags){
 if(pending){kfree(pending);pending=NULL;}
 assert(!running);f->flags=flags;f->timeout=now+3000;f->ct->dying=false;
 unlinked=unsafe_unlink=adds=deletes=stats=accounted=0;
 late_pending=late_dying=fail_alloc=bridge_changed=worker_gc=false;
}
static void complete(void){
 assert(pending);running=pending;pending=NULL;
 running->work.func(&running->work);running=NULL;
}
int main(void){
 struct net net={0};struct nf_flowtable t={.net=&net,.flags=NF_FLOWTABLE_COUNTER};
 struct nf_conn ct={0};struct flow_offload f={.ct=&ct};table=&t;now=100000;
 reset(&f,BIT(NF_FLOW_HW)|BIT(NF_FLOW_CLOSING));
 nf_flow_offload_gc_step(&t,&f,NULL);assert(pending&&pending->cmd==FLOW_CLS_DESTROY);
 complete();assert(deletes==1&&test_bit(NF_FLOW_HW_DEAD,&f.flags));
 now+=400;nf_flow_offload_gc_step(&t,&f,NULL);
 bool dead_stats=pending&&pending->cmd==FLOW_CLS_STATS;
 set_bit(NF_FLOW_TEARDOWN,&f.flags);nf_flow_offload_gc_step(&t,&f,NULL);
 bool closing_uaf=unsafe_unlink!=0;
 reset(&f,BIT(NF_FLOW_HW)|BIT(NF_FLOW_HW_DYING)|BIT(NF_FLOW_HW_DEAD)|BIT(NF_FLOW_TEARDOWN));
 late_pending=true;nf_flow_offload_gc_step(&t,&f,NULL);bool late_uaf=unlinked!=0;
 reset(&f,BIT(NF_FLOW_HW)|BIT(NF_FLOW_HW_DYING)|BIT(NF_FLOW_HW_DEAD)|BIT(NF_FLOW_TEARDOWN)|BIT(NF_FLOW_HW_PENDING));
 nf_flow_offload_gc_step(&t,&f,NULL);bool pending_uaf=unlinked!=0;
 reset(&f,BIT(NF_FLOW_HW)|BIT(NF_FLOW_HW_DYING)|BIT(NF_FLOW_HW_DEAD)|BIT(NF_FLOW_TEARDOWN));
 nf_flow_offload_gc_step(&t,&f,NULL);assert(unlinked==1);
 struct flow_offload_work *w=nf_flow_offload_work_alloc(&t,&f,FLOW_CLS_REPLACE);
 bool retired_admission=w!=NULL;if(w)kfree(w);
 bool dying_admission=false;
 for(unsigned cmd=FLOW_CLS_REPLACE;cmd<=FLOW_CLS_STATS;cmd++){
  reset(&f,BIT(NF_FLOW_HW));late_dying=true;
  w=nf_flow_offload_work_alloc(&t,&f,cmd);dying_admission|=w!=NULL;if(w)kfree(w);
 }
 printf("dead_stats=%u closing_uaf=%u late_pending_uaf=%u pending_uaf=%u retired_admission=%u dying_admission=%u\n",
  dead_stats,closing_uaf,late_uaf,pending_uaf,retired_admission,dying_admission);
#ifdef EXPECT_BEFORE
 assert(dead_stats&&closing_uaf&&late_uaf&&pending_uaf&&retired_admission&&dying_admission);
#else
 assert(!dead_stats&&!closing_uaf&&!late_uaf&&!pending_uaf&&!retired_admission&&!dying_admission);
#endif
 /* Active ADD/STATS, allocation retries and delayed DESTROY must still progress. */
 for(unsigned i=0;i<2000;i++){
  reset(&f,BIT(NF_FLOW_HW)|BIT(NF_FLOW_HW_BIDIRECTIONAL));
  fail_alloc=true;nf_flow_offload_add(&t,&f);assert(!pending&&!test_bit(NF_FLOW_HW_PENDING,&f.flags));
  fail_alloc=false;nf_flow_offload_add(&t,&f);assert(pending);complete();assert(adds==1);
  now+=400;nf_flow_offload_gc_step(&t,&f,NULL);assert(pending&&pending->cmd==FLOW_CLS_STATS);
  nf_flow_offload_add(&t,&f);assert(pending->cmd==FLOW_CLS_STATS);
  if(i%4==0)set_bit(NF_FLOW_TEARDOWN,&f.flags);
  if(i%4==1)ct.dying=true;
  if(i%4==2)bridge_changed=true;
  if(i%4==3)f.timeout=now;
  nf_flow_offload_gc_step(&t,&f,NULL);assert(!unlinked);
  complete();assert(stats==2&&accounted==2&&!test_bit(NF_FLOW_HW_PENDING,&f.flags));
  /* STATS may refresh an expired timeout; emulate the later idle expiry. */
  if(i%4==3)now=f.timeout;
  fail_alloc=true;nf_flow_offload_gc_step(&t,&f,NULL);
  assert(!pending&&!unlinked&&!test_bit(NF_FLOW_HW_DYING,&f.flags));
  fail_alloc=false;nf_flow_offload_gc_step(&t,&f,NULL);
  assert(pending&&pending->cmd==FLOW_CLS_DESTROY);
  nf_flow_offload_gc_step(&t,&f,NULL);assert(!unlinked);
  worker_gc=true;complete();assert(deletes==1&&!unlinked);
  nf_flow_offload_gc_step(&t,&f,NULL);assert(unlinked==1&&!unsafe_unlink);
 }
 reset(&f,0);f.timeout=now;nf_flow_offload_gc_step(&t,&f,NULL);
 assert(unlinked==1&&!pending&&!test_bit(NF_FLOW_HW_PENDING,&f.flags));
 puts("PASS: 2000 active/teardown cycles, stats/accounting, allocation retry, destroy handoff, software expiry");
}
'''

with tempfile.TemporaryDirectory(prefix="flow-lifetime-") as directory:
    work = Path(directory)
    names = ["net/netfilter/nf_flow_table_core.c", "net/netfilter/nf_flow_table_offload.c"]
    for name in names:
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.kernel / name, dest)
    if args.before:
        patch = root / "port/overlay/target/linux/rtl9617c/patches-6.18/218-flowtable-work-lifetime.patch"
        subprocess.run(["patch", "-R", "-p1", "--fuzz=0", "-i", str(patch)], cwd=work, check=True)
    core, offload = [(work / name).read_text() for name in names]
    parts = [common]
    for name in ["flow_offload_work_stats", "flow_offload_work_handler", "nf_flow_offload_work_alloc",
                 "nf_flow_offload_add", "nf_flow_offload_stats", "nf_flow_offload_del"]:
        parts.append(function(offload, name))
    parts += [function(core, "nf_flow_offload_gc_step"), cases]
    (work / "test.c").write_text("\n".join(parts))
    command = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
               "-Wno-unused-function", "-Wno-sign-compare", "-fsanitize=address,undefined", "-g"]
    if args.before:
        command.append("-DEXPECT_BEFORE")
    subprocess.run(command + [str(work / "test.c"), "-o", str(work / "test")], check=True)
    subprocess.run([str(work / "test")], check=True)
