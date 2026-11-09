#!/usr/bin/env python3
"""Execute QM start's real reset gate before BID/ring reinitialization."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--stock',type=Path)
p.add_argument('--revision')
p.add_argument('--failure',type=int,choices=range(1,7),help='isolate one startup failure in an old revision')
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
original=[]
if a.stock:
    from arm64_module_oracle import ModuleOracle
    from unicorn import UC_HOOK_MEM_READ
    def boundary(name,args):raise AssertionError((name,args))
    o=ModuleOracle(a.stock,['aal_l3qm_check_init_done'],boundary)
    base=0x6000000;o.uc.mem_map(base,65536)
    o.word(o.symbols['g_ne_reg_iobase'][0],base)
    count=0;ready=1
    def read(u,access,addr,size,value,data):
        global count
        assert addr==base+0x6850 and size==4
        count+=1;u.mem_write(addr,((1<<30) if count>=ready else 0).to_bytes(4,'little'))
    o.uc.hook_add(UC_HOOK_MEM_READ,read,begin=base,end=base+65535)
    for ready in (1,2,31,300):
        count=0;assert o.call('aal_l3qm_check_init_done',instruction_budget=10000)==0 and count==ready
        original.append(count)

relative='port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c'
source=subprocess.check_output(['git','show',a.revision+':'+relative],cwd=root,text=True) if a.revision else (root/relative).read_text()
start=source.index('\trx->deepq_configured = false;',source.index('int rtl_qm_rx_start('))
end=source.index('\t/* TQM reset invalidates old BIDs;',start)
gate=source[start:end]
code=(f'#define TEST_FAILURE {a.failure}\n' if a.failure else '')+r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define CB_CTRL 0x950c
#define RMU_CTRL 0x6104
#define ES_CTRL 0x6108
struct rtl_qm_rx {void *ni,*block_reset;bool deepq_configured;};
static u32 regs[65536/4],reset_word;
static unsigned writes,skip_write,ready_reads,delays,reinitializations;
static bool stuck_ready,mutate;
static u32 readl(void *p){
 if(p==regs+0x6850/4){ready_reads++;return stuck_ready?0:BIT(30);}
 return *(u32*)p;
}
static void writel(u32 value,void *p){writes++;if(writes!=skip_write)*(u32*)p=value;}
static void udelay(unsigned delay){assert(delay==1000);delays++;if(mutate)reset_word^=BIT(12);}
#define readl_poll_timeout(p,v,c,d,t) ({unsigned n=0;int error=-ETIMEDOUT;(void)(d);(void)(t);do{v=readl(p);if(c){error=0;break;}}while(++n<4);error;})
static int start(struct rtl_qm_rx *rx){void *ni=rx->ni;u32 value;int ret;
'''+gate+r'''
 /* This boundary immediately precedes the production refill clear and
  * all ring/EQ reinitialization. It must not be reached on a failed gate. */
 reinitializations++;return 0;
}
static void setup(unsigned failure,bool changed){
 memset(regs,0,sizeof(regs));regs[CB_CTRL/4]=0xabcdef01;regs[RMU_CTRL/4]=0x87654321;regs[ES_CTRL/4]=0x8462ff0f;
 reset_word=0xa5000011;writes=ready_reads=delays=reinitializations=0;skip_write=failure;mutate=changed;stuck_ready=false;
}
int main(void){unsigned cases=0;
 for(unsigned changed=0;changed<2;changed++)for(unsigned fail=0;fail<=6;fail++){
#ifdef TEST_FAILURE
  if(fail!=TEST_FAILURE)continue;
#endif
  setup(fail<6?fail:0,changed);stuck_ready=fail==6;
  struct rtl_qm_rx rx={.ni=regs,.block_reset=&reset_word,.deepq_configured=true};
  int ret=start(&rx);
  assert(ret==(fail==6?-ETIMEDOUT:fail?-EIO:0));
  assert(reinitializations==!fail && !rx.deepq_configured);
  if(fail<=3 && fail){assert(writes==fail && !delays && !ready_reads && reset_word==0xa5000011);}
  if(fail==4){assert(writes==4 && !delays && !ready_reads);}
  if(fail==5){assert(writes==5 && delays==1 && !ready_reads && (reset_word&BIT(5)));}
  if(!fail||fail==6){assert(writes==5 && delays==1 && ready_reads==(fail?4:1));assert(!(reset_word&BIT(5)));}
  assert((reset_word&~BIT(5))==(0xa5000011^(changed && (fail==0||fail>=5)?BIT(12):0)));
  if(fail){
   skip_write=0;stuck_ready=mutate=false;
   assert(!start(&rx) && reinitializations==1 && !(reset_word&BIT(5)));
  }
  assert(regs[CB_CTRL/4]==0x2bcdef01 && regs[RMU_CTRL/4]==0x07654321 && regs[ES_CTRL/4]==0x0462ff0f);
  cases++;
 }
 /* A previous init-done high level must not authorize reuse on either
  * failed reset edge; a reset already held may be asserted again safely. */
 for(unsigned bit=0;bit<32;bit++)if(bit!=5){
  setup(0,false);reset_word=BIT(bit)|BIT(5);
  struct rtl_qm_rx rx={.ni=regs,.block_reset=&reset_word};
  assert(!start(&rx) && reset_word==BIT(bit) && reinitializations==1);cases++;
 }
 printf("QM startup reset: %u gate/reset edge/ready/retry/unrelated-bit cases PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='qm-reset-gate-') as name:
    d=Path(name);(d/'test.c').write_text(code)
    subprocess.run(['cc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(d/'test.c'),'-o',str(d/'test')],check=True)
    result=subprocess.run([str(d/'test')],capture_output=True,text=True)
    if a.revision:
        assert result.returncode and 'ret==' in result.stderr,result
        print('REPRODUCED: old QM start accepts failed control/reset write and stale INIT_DONE')
    else:
        assert result.returncode==0,result.stderr
        print(result.stdout,end='')
a.output.write_text(json.dumps(dict(original_ready_reads=original,revision=a.revision,failure=a.failure,native_passed=result.returncode==0,
    scope='Actual production startup prefix through reset/ready; modeled MMIO and BID reinitialization boundary, not full hardware reset or stop/free retirement proof.'),indent=2)+'\n')
