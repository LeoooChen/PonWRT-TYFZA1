#!/usr/bin/env python3
"""Execute CPU EQ refill with real credit depletion and independent FIFO stalls."""
from pathlib import Path
import argparse
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path)
args = parser.parse_args()
source = (args.source or driver / 'rtl9617c-qm.c').read_text()
start = source.index('static int rtl_qm_try_push(')
end = source.index('\nstatic int rtl_qm_ingress(', start)
model = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define EQ_ID 4
#define CPU_READY 0x6380
#define CPU_PUSH 0x6384
#include "rtl9617c-qm-refill.h"
struct rtl_qm_rx {void *ni;struct rtl_qm_refill refill;};
static u32 registers[0x10000/4],credit[2],reads[2],fifo_reads,pushed[2],stop_after,add_after,ready;
static u32 readl(void *p) {
 unsigned reg=(char *)p-(char *)registers;
 if(reg==0x6338||reg==0x633c){unsigned eq=(reg-0x6338)/4;reads[eq]++;return credit[eq];}
 assert(reg==CPU_READY);fifo_reads++;return ready?BIT(31):0;
}
static void writel(u32 value,void *p) {
 assert((char *)p-(char *)registers==CPU_PUSH);
 unsigned eq=(value&15)-4;assert(eq<2&&ready&&credit[eq]);
 assert((value&~127U)==0x4000000+eq*0x100000+pushed[eq]*128);
 pushed[eq]++;credit[eq]--;
 if(pushed[0]+pushed[1]==add_after)credit[0]+=64;
 if(pushed[0]+pushed[1]==stop_after)ready=0;
}
"""
cases = r"""
static void setup(struct rtl_qm_rx *rx,unsigned n0,unsigned n1,unsigned c0,unsigned c1) {
 memset(rx,0,sizeof(*rx));rx->ni=registers;
 memset(reads,0,sizeof(reads));memset(pushed,0,sizeof(pushed));fifo_reads=0;
 credit[0]=c0;credit[1]=c1;ready=1;stop_after=add_after=~0U;
 for(unsigned e=0;e<2;e++)for(unsigned i=0;i<(e?n1:n0);i++)
  rtl_qm_refill_add(&rx->refill,(0x4000000+e*0x100000+i*128)|(e+4));
}
int main(void) {
 struct rtl_qm_rx rx;unsigned count=0;
 for(unsigned c0=0;c0<=64;c0+=8)for(unsigned c1=0;c1<=64;c1+=8)
 for(unsigned stop=0;stop<=64;stop+=8){
  setup(&rx,64,64,c0,c1);stop_after=stop;if(!stop)ready=0;
  rtl_qm_refill_drain(&rx.refill,&rx,rtl_qm_try_push,64);
  assert(pushed[0]<=c0&&pushed[1]<=c1&&pushed[0]+pushed[1]<=64);
  assert(rx.refill.pending+rx.refill.returned==128);
  assert(rx.refill.count[0]+pushed[0]==64&&rx.refill.count[1]+pushed[1]==64);
  /* Each later poll takes fresh credit; all retained suffixes drain once. */
  credit[0]=credit[1]=64;ready=1;stop_after=~0U;
  while(rx.refill.pending)rtl_qm_refill_drain(&rx.refill,&rx,rtl_qm_try_push,64);
  assert(pushed[0]==64&&pushed[1]==64&&rx.refill.returned==128);count++;
 }
 setup(&rx,64,0,64,0);rtl_qm_refill_drain(&rx.refill,&rx,rtl_qm_try_push,64);
 unsigned full_reads=reads[0];assert(pushed[0]==64&&fifo_reads==64);
 setup(&rx,64,0,8,0);add_after=8;rtl_qm_refill_drain(&rx.refill,&rx,rtl_qm_try_push,64);
 assert(pushed[0]==64&&reads[0]>=2); /* New credit is observed on exhaustion. */
 setup(&rx,2,0,64,0);rtl_qm_refill_drain(&rx.refill,&rx,rtl_qm_try_push,64);
 /* Unused cached allowance must not survive a later call. */
 rtl_qm_refill_add(&rx.refill,(0x4000000+2*128)|4);credit[0]=0;
 unsigned before=reads[0];rtl_qm_refill_drain(&rx.refill,&rx,rtl_qm_try_push,64);
 assert(pushed[0]==2&&rx.refill.pending==1&&reads[0]==before+1);
 credit[0]=1;rtl_qm_refill_drain(&rx.refill,&rx,rtl_qm_try_push,64);assert(pushed[0]==3);
 setup(&rx,64,64,0,64);rtl_qm_refill_drain(&rx.refill,&rx,rtl_qm_try_push,64);
 assert(!pushed[0]&&pushed[1]==63&&rx.refill.count[0]==64);
 printf("%u dual-EQ credit/FIFO/resume cases; full64 EQ reads=%u, FIFO reads=64; cross-drain reset and EQ isolation passed\n",count,full_reads);
 assert(full_reads==1);
}
"""
with tempfile.TemporaryDirectory(prefix='qm-refill-credit-', dir=root / 'artifacts') as temp:
    c, exe = Path(temp) / 'test.c', Path(temp) / 'test'
    c.write_text(model + source[start:end] + cases)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-I', str(driver), str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
