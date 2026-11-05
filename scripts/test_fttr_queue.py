#!/usr/bin/env python3
"""Compare both P6 DEEPQ/ES queue stages with stock and expose incomplete drains."""
import argparse
import ctypes
import errno
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from test_fttr_p6 import function
from trace_stock_fttr_queue import trace


SHIM = r'''
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <string.h>
typedef uint32_t u32; typedef uint8_t u8;
#define __iomem
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U >> (31-(h))) & (~0U << (l)))
#include "rtl9617c-fttr-p6.h"
static u32 reg[0x4000];
static struct rtl_fttr_p6 p6;
static const unsigned int controls[2]={0x63fc/4,0x234c/4},occupancy[2]={0x683c/4,0x23d8/4};
static unsigned int queue_reads[2], active_queue[2], stage_writes[2], events[40][2], count;
static int fault, failed_stage;
static u32 readl(void *p)
{
 unsigned int address=(u32 *)p-reg;
 for(unsigned int stage=0;stage<2;stage++) {
  if(address!=occupancy[stage])continue;
  if(reg[controls[stage]]&BIT(15)) {
   queue_reads[stage]++;
   if(!(stage==(unsigned)failed_stage && (fault==1 || fault==5) && active_queue[stage]==3) && queue_reads[stage]>3)
    reg[address]&=~BIT(active_queue[stage]+16);
  }
  if(fault==4 && stage==(unsigned)failed_stage && stage_writes[stage]==17)reg[address]|=BIT(16);
  /* Refill L3 only after L2 completed, beyond the first L3 final check. */
  if(fault==6 && stage==0 && count==34)reg[address]|=BIT(20);
 }
 return reg[address];
}
static void writel(u32 value,void *p)
{
 unsigned int address=(u32 *)p-reg;
 events[count][0]=address*4;events[count++][1]=value;
 for(unsigned int stage=0;stage<2;stage++) {
  if(address!=controls[stage])continue;
  stage_writes[stage]++;
  if(stage==(unsigned)failed_stage && ((fault==2 && (value&BIT(15))) ||
     (fault==3 && stage_writes[stage]==17) || ((fault==5 || fault==7) && stage_writes[stage]==8)))return;
  reg[address]=value;
  if(value&BIT(15)) {
   active_queue[stage]=(value>>8)&7;queue_reads[stage]=0;
  }
 }
}
/* Exercise both exits of the production poll expression. Kernel elapsed-time
 * scheduling is not modeled; this count bounds only the emulated completion. */
#define readl_poll_timeout(addr,value,condition,delay,timeout) ({ \
 int result=-ETIMEDOUT; \
 for(unsigned int n=0;n<8;n++) { value=readl(addr);if(condition){result=0;break;} } \
 result; })
'''

WRAPPER = r'''
int run(int mode,int stage,unsigned int l3_seed,unsigned int l2_seed,unsigned int occupied)
{
 memset(reg,0,sizeof(reg)); memset(&p6,0,sizeof(p6));
 p6.ni=reg;fault=mode;failed_stage=stage;count=0;
 memset(queue_reads,0,sizeof(queue_reads));memset(active_queue,0,sizeof(active_queue));memset(stage_writes,0,sizeof(stage_writes));
 reg[controls[0]]=l3_seed;reg[controls[1]]=l2_seed;
 reg[occupancy[0]]=reg[occupancy[1]]=occupied;
 return rtl_fttr_p6_flush_queues(&p6);
}
void result(int stage,int *out)
{
 struct rtl_fttr_queue_state *state=stage?&p6.l2_queue:&p6.l3_queue;
 out[0]=state->completed;out[1]=state->failed;out[2]=state->error;
 out[3]=state->cleanup_error;out[4]=state->status;out[5]=reg[controls[stage]];
}
unsigned int event_count(void){return count;}
unsigned int event(unsigned int index,unsigned int word){return events[index][word];}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ni', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    headers = root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    source = (headers/'rtl9617c-fttr-p6.c').read_text()
    seed = 0xa55a20ff
    originals = [trace(args.ni, cos, busy, seed, layer)
                 for layer in (3, 2) for cos, busy in [(0, 0), (7, 3), (3, 10000)]]
    records = []
    with tempfile.TemporaryDirectory(dir=root/'artifacts/fttr') as directory:
        directory = Path(directory)
        (directory/'queue.c').write_text(SHIM+'\n'+function(source,'p6_update')+'\n'+
            function(source,'p6_flush_stage')+'\n'+
            function(source,'rtl_fttr_p6_flush_queues')+'\n'+WRAPPER)
        subprocess.run(['cc','-O2','-shared','-fPIC','-Wall','-Wextra','-Werror','-I'+str(headers),
            str(directory/'queue.c'),'-o',str(directory/'queue.so')],check=True)
        native = ctypes.CDLL(str(directory/'queue.so'))
        native.run.argtypes = [ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint]
        native.event.restype = ctypes.c_uint
        for failed_stage in (0,1):
            for case, fault, occupancy, expected in [
                ('empty',0,0,0), ('drained',0,0x00ff0000,0),
                ('neighbor-ports-occupied',0,0xff00ffff,0),
                ('stuck',1,0x00ff0000,-errno.ETIMEDOUT),
                ('command-not-applied',2,0x00ff0000,-errno.EIO),
                ('restore-not-applied',3,0,-errno.EIO),
                ('stuck-and-restore-failed',5,0x00ff0000,-errno.ETIMEDOUT),
                ('flush-disable-not-applied',7,0,-errno.EIO),
                ('refilled',4,0,-errno.EAGAIN),
                ('l3-refilled-during-l2',6,0,-errno.EAGAIN),
                ('already-flushing',0,0,-errno.EBUSY)]:
                seeds=[seed,seed]
                if case=='already-flushing': seeds[failed_stage]|=0x8000
                ret = native.run(fault,failed_stage,*seeds,occupancy)
                states=[]
                for stage in (0,1):
                    state = (ctypes.c_int*6)();native.result(stage,state);states.append(list(state))
                    if stage!=failed_stage or fault not in (3,5):assert state[5]&0xffffffff==seeds[stage]
                state=states[failed_stage]
                writes = [[native.event(i,0),native.event(i,1)] for i in range(native.event_count())]
                assert ret==expected,(case,failed_stage,ret,states)
                if not expected:
                    assert all(s[0]==255 and s[4]&0xff0000==0 for s in states)
                    assert all(s[4]&0xff00ffff==occupancy&0xff00ffff for s in states)
                    for original in originals:
                        stage=0 if original['layer']==3 else 1;cos=original['cos'];start=stage*17+cos*2
                        assert writes[start:start+2]==original['writes']
                    assert writes[16]==[0x63fc,seed] and writes[-1]==[0x234c,seed]
                if case=='stuck':
                    assert state[0]==7 and state[1]==3 and state[2]==-errno.ETIMEDOUT and state[3]==0
                    original=originals[failed_stage*3+2]
                    assert state[4]&(1<<19) and original['last_status']&(1<<19)
                    assert original['return']==0 and len(writes)==failed_stage*17+8
                if case=='restore-not-applied':assert state[2]==0 and state[3]==-errno.EIO
                if case=='stuck-and-restore-failed':assert state[2]==-errno.ETIMEDOUT and state[3]==-errno.EIO
                if case=='flush-disable-not-applied':assert state[0]==15 and state[1]==3 and state[3]==0
                if case=='already-flushing':assert not writes
                if failed_stage==0 and expected and fault!=6:
                    assert states[1][0]==0 and all(w[0]!=0x234c for w in writes)
                if fault==6: assert states[0][0]==states[1][0]==255 and states[0][2]==-errno.EAGAIN
                records.append({'case':case,'failure_stage':failed_stage,'return':ret,
                    'l3_state':states[0], 'l2_state':states[1], 'writes':len(writes)})
    report = {'native_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'stock_module_sha256':hashlib.sha256(args.ni.read_bytes()).hexdigest(),
        'original':originals,'native':records,
        'scope':'Production P6 L3-DEEPQ and L2 ES-flush functions versus actual stock ARM64. Occupancy/write completion and poll time are modeled. '
            'This does not verify DMA prefetch/PE/MiniOLT buffers or optical forwarding.'}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print('P6 L3/L2 ES: original false-success reproduced; both stages, timeout, restoration, neighbor preservation and cross-stage refill passed')


if __name__ == '__main__':
    main()
