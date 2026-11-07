#!/usr/bin/env python3
"""Execute actual ARM PE-discard work and ownership completion under sanitizers."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
fixture = root / 'scripts/test_radio_rpq_batch.py'
setup = fixture.read_text().split("code+=before.replace", 1)[0]
env = {'__file__': str(fixture), '__name__': 'fttr_completion_fixture'}
exec(compile(setup, str(fixture), 'exec'), env)
source, code = env['source'], env['code']
code = code.replace('struct rtl_ax_tx {', 'struct rtl_ax_tx {struct delayed_work fttr_work;')
code = '''
#include <stddef.h>
struct work_struct {int unused;};struct delayed_work {struct work_struct work;};
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
#define for_each_set_bit(i,b,n) for(i=0;i<n;i++)if(b[i])
#define WRITE_ONCE(v,x) ((v)=(x))
#define cpu_to_le32(x) (x)
#define system_wq 0
''' + code
code += '''
static unsigned requeues,barriers;
static void queue_delayed_work(int q,struct delayed_work *w,int delay){(void)q;(void)w;assert(delay==1);requeues++;}
static void dma_rmb(void){barriers++;}
static void dma_wmb(void){barriers++;}
'''
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/include/linux/rtl9617c-fttr-pe.h'
code += header.read_text()
code += env['after']
start = re.search(r'^static void rtl_ax_tx_fttr_work\(struct work_struct \*work\)\n\{', source, re.M).start()
end = source.index('\nstatic bool rtl_ax_tx_pe_pending', start)
code += source[start:end]
code += r'''
static void put(void *p,u32 v){memcpy(p,&v,4);}
int main(void){
 static u8 memory[0x20000];static struct sk_buff skbs[256];
 struct ieee80211_hw hw={};unsigned cases=0;
 for(unsigned released=0;released<=256;released++) {
  memset(memory,0,sizeof(memory));memset(skbs,0,sizeof(skbs));
  struct rtl_ax_tx t={.pe_active=true,.running=true,.queues_stopped=true,.hw=&hw,.pe={memory},.occupied=256};
  active=&t;batch=1;expected=released;bh=rcu=locks=wakes=unmaps=returns=raws=sw_freed=0;
  requeues=barriers=0;runnable=false;
  for(unsigned id=0;id<256;id++) {
   skbs[id].data[0]=id;skbs[id].rtl9617c_fttr_generation=2;
   skbs[id].cb.flags=IEEE80211_TX_CTL_HW_80211_ENCAP|IEEE80211_TX_CTL_AMPDU|IEEE80211_TX_STAT_ACK;
   t.pending[id]=true;t.slots[id]=(struct rtl_ax_tx_slot){.skb=&skbs[id],.ring=id%6,.macid=id%128,.pe_sequence=true};
   if(id<released)put(memory+RTL_FTTR_PE_DISCARD+id*4,1);
  }
  /* No headers allocation: canceled requests never produced sequence data. */
  rtl_ax_tx_fttr_work(&t.fttr_work.work);
  assert(t.fttr_discarded==released&&sw_freed==released&&unmaps==released);
  assert(t.completed==released&&t.occupied==256-released&&t.status_count[4]==released);
  assert(!returns&&!raws&&!lookups&&!t.ampdu_completed&&!t.unmatched);
  assert(requeues==(released<256)&&barriers==released*2);
  for(unsigned id=0;id<256;id++) {
   assert(!get_unaligned_le32(memory+RTL_FTTR_PE_DISCARD+id*4));
   assert(t.pending[id]==(id>=released));
   assert(!t.peer_success[id%128].calls);
  }
  /* A completed WP reused by a non-FTTR skb must not be polled/dropped. */
  if(released) {
   skbs[0].rtl9617c_fttr_generation=0;t.slots[0].skb=&skbs[0];t.pending[0]=true;t.occupied++;
   rtl_ax_tx_fttr_work(&t.fttr_work.work);
   assert(t.fttr_discarded==released&&t.pending[0]&&sw_freed==released);
  }
  cases++;
 }
 printf("ARM FTTR PE completion: %u partial/full batches, all WP IDs/rings, unmap/free once, no ACK/rate/sequence feedback, retry and reuse passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='fttr-pe-completion-') as tmp:
    path = Path(tmp)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
