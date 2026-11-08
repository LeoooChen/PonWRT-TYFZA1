#!/usr/bin/env python3
"""Actual FE callback/index with Linux's userspace rbtree and injected retries.

--before runs the same workload against the original linear callback and
expects the traversal bound to fail. Hardware access/concurrency are not modeled.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--kernel', type=Path, required=True)
p.add_argument('--before', action='store_true')
p.add_argument('--baseline', default='v0.8.4-beta', help='Unindexed source for --before')
p.add_argument('--emit-c', type=Path, help='Write the same harness for target compilation')
a = p.parse_args()
driver = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-fe.c'
source = (root / driver).read_text()
old = (subprocess.check_output(['git', 'show', a.baseline + ':' + driver],
                               cwd=root, text=True) if a.before else None)

def function(text, name):
    m = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', text, re.M)
    end = text.index('{', m.start()) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[m.start():end] + '\n'

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <linux/list.h>
#include <linux/rbtree.h>
static unsigned long long visits, deleted, stats;
/* Count list-node visits without replacing the production loop itself. */
#undef list_for_each_entry
#define list_for_each_entry(p,h,m) for(p=list_first_entry(h,__typeof__(*p),m); \
 &p->m!=(h) && (++visits); p=list_next_entry(p,m))
#undef list_for_each_entry_safe
#define list_for_each_entry_safe(p,n,h,m) \
 for(p=list_first_entry(h,__typeof__(*p),m),n=list_next_entry(p,m); \
 &p->m!=(h) && (++visits); p=n,n=list_next_entry(n,m))
#undef rb_entry
#define rb_entry(p,t,m) (++visits,container_of(p,t,m))
struct rtl_fe;
struct rtl_fe_port {struct rtl_fe *fe;};
struct rtl_fe_flow {struct list_head list,retry;struct rb_node cookie_node;
 unsigned long cookie;bool installed,deleting;struct rtl_fe_port *input;};
struct rtl_fe {struct list_head flows,retries;struct rb_root cookies;
 unsigned int lock,misses;};
struct flow_cls_offload {struct {unsigned int chain_index;}common;
 unsigned long cookie;unsigned int command;};
enum tc_setup_type {TC_SETUP_CLSFLOWER};
enum {FLOW_CLS_REPLACE,FLOW_CLS_DESTROY,FLOW_CLS_STATS};
static bool fail_delete;
static void mutex_lock(unsigned int *v){assert(!*v);*v=1;}
static void mutex_unlock(unsigned int *v){assert(*v);*v=0;}
static int fe_delete(struct rtl_fe *fe,struct rtl_fe_flow *f){
 if(fail_delete)return -ETIMEDOUT;
 rb_erase(&f->cookie_node,&fe->cookies);list_del_init(&f->retry);
 list_del_init(&f->list);deleted++;return 0;
}
static int fe_stats(struct rtl_fe *fe,struct rtl_fe_flow *f,struct flow_cls_offload *c){
 assert(fe->lock && f->cookie==c->cookie);stats++;return 0;
}
static int fe_replace(struct rtl_fe_port *p,struct flow_cls_offload *c){(void)p;(void)c;return 0;}
static void fe_collect_ports(struct rtl_fe *fe){assert(fe->lock);}
'''
test = r'''
int main(void){
 const unsigned int count=16384;
 struct rtl_fe fe={.cookies=RB_ROOT};struct rtl_fe_port port={.fe=&fe},other={.fe=&fe};
 struct rtl_fe_flow *f=calloc(count,sizeof(*f));assert(f);
 INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);
 for(unsigned int i=0;i<count;i++){
  f[i].cookie=(unsigned long)i*4096;f[i].installed=true;f[i].input=&port;
  INIT_LIST_HEAD(&f[i].retry);list_add_tail(&f[i].list,&fe.flows);fe_index_flow(&fe,&f[i]);
 }
 visits=0;clock_t start=clock();
 for(unsigned int i=0;i<count;i++){
  struct flow_cls_offload c={.cookie=f[i].cookie,.command=FLOW_CLS_STATS};
  assert(!fe_block_callback(TC_SETUP_CLSFLOWER,&c,&port));
 }
 printf("flows=%u stats=%llu node_visits=%llu cpu_ms=%.3f\n",count,stats,visits,
  1000.0*(clock()-start)/CLOCKS_PER_SEC);fflush(stdout);
 assert(visits<(unsigned long long)count*64);
 assert(!fe_flow(&fe,7));assert(fe_flow(&fe,0)==&f[0]);
 struct flow_cls_offload c={.cookie=f[count-1].cookie,.command=FLOW_CLS_DESTROY};
 assert(fe_block_callback(TC_SETUP_CLSFLOWER,&c,&other)==-ENOENT);
 /* One failed deletion remains indexed and queued exactly once. */
 f[17].deleting=true;list_add_tail(&f[17].retry,&fe.retries);fail_delete=true;
 assert(fe_block_callback(TC_SETUP_CLSFLOWER,&c,&port)==-ETIMEDOUT);
 assert(fe_flow(&fe,f[17].cookie)==&f[17]);assert(fe.retries.next==&f[17].retry);
 fail_delete=false;assert(!fe_block_callback(TC_SETUP_CLSFLOWER,&c,&port));
 assert(deleted==2 && list_empty(&fe.retries));assert(!fe_flow(&fe,c.cookie));
 /* Reuse the deleted cookie, then rekey exactly as the bridge learner does. */
 f[count-1].installed=true;list_add_tail(&f[count-1].list,&fe.flows);
 fe_index_flow(&fe,&f[count-1]);assert(fe_flow(&fe,c.cookie)==&f[count-1]);
 rb_erase(&f[count-1].cookie_node,&fe.cookies);f[count-1].cookie=~0UL;
 fe_index_flow(&fe,&f[count-1]);assert(!fe_flow(&fe,c.cookie));
 assert(fe_flow(&fe,~0UL)==&f[count-1]);
 /* Delete/reinsert in a full permutation, checking every selected lookup. */
 for(unsigned int i=0;i<count;i++){
  unsigned int j=(i*4051U)&(count-1);
  if(j==17)continue;
  unsigned long key=f[j].cookie;
  assert(fe_flow(&fe,key)==&f[j]);fe_delete(&fe,&f[j]);
  assert(!fe_flow(&fe,key));
  list_add_tail(&f[j].list,&fe.flows);fe_index_flow(&fe,&f[j]);
  assert(fe_flow(&fe,key)==&f[j]);
 }
 /* Multiple queued releases must all leave the index and retry list. */
 f[3].deleting=true;f[5].deleting=true;
 list_add_tail(&f[3].retry,&fe.retries);list_add_tail(&f[5].retry,&fe.retries);
 c.cookie=f[9].cookie;c.command=FLOW_CLS_STATS;
 assert(!fe_block_callback(TC_SETUP_CLSFLOWER,&c,&port));
 assert(list_empty(&fe.retries)&&!fe_flow(&fe,f[3].cookie)&&!fe_flow(&fe,f[5].cookie));
 for(unsigned int i=0;i<count;i++)if(i!=17&&i!=3&&i!=5)fe_delete(&fe,&f[i]);
 assert(RB_EMPTY_ROOT(&fe.cookies)&&list_empty(&fe.flows));free(f);
 puts("FE indexed lookup, zero/high/reused cookies, bridge rekey, wrong-port destroy and retry passed");
}
'''
text = old if a.before else source
parts = [common, function(text, 'fe_flow'), function(source, 'fe_index_flow'),
         function(text, 'fe_block_callback'), test]
if a.emit_c:
    a.emit_c.write_text('\n'.join(parts))
    raise SystemExit(0)
with tempfile.TemporaryDirectory() as td:
    c = Path(td)/'index.c'; binary = Path(td)/'index'
    c.write_text('\n'.join(parts))
    subprocess.run(['cc','-O2','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',
                    '-I'+str(a.kernel/'tools/include'),str(c),str(a.kernel/'tools/lib/rbtree.c'),
                    '-o',str(binary)],check=True)
    r = subprocess.run([str(binary)],text=True,capture_output=True)
    print(r.stdout,end='')
    if a.before:
        assert r.returncode and 'visits<' in r.stderr, r.stderr
        print('Original source fails the same traversal bound as expected')
    else:
        if r.returncode: print(r.stderr,end='')
        r.check_returncode()
