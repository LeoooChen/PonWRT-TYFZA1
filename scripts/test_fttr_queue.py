#!/usr/bin/env python3
"""Compare P6 ES flush writes with original code and expose incomplete drains."""
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
static unsigned int reads, queue_reads, active_queue, events[32][2], count;
static int fault;
static u32 readl(void *p)
{
 unsigned int address=(u32 *)p-reg;
 if(address==0x23d8/4) {
  reads++;
  if(reg[0x234c/4]&BIT(15)) {
   queue_reads++;
   if(!((fault==1 || fault==5) && active_queue==3) && queue_reads>3)
    reg[address]&=~BIT(active_queue+16);
  }
  if(fault==4 && count==17)reg[address]|=BIT(16); /* Arrives after the last per-COS flush. */
 }
 return reg[address];
}
static void writel(u32 value,void *p)
{
 unsigned int address=(u32 *)p-reg;
 events[count][0]=address*4;events[count++][1]=value;
 if((fault==2 && (value&BIT(15))) || (fault==3 && count==17) || (fault==5 && count==8))return;
 reg[address]=value;
 if(address==0x234c/4 && (value&BIT(15))) {
  active_queue=(value>>8)&7;queue_reads=0;
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
int run(int mode,unsigned int seed,unsigned int occupied)
{
 memset(reg,0,sizeof(reg)); memset(&p6,0,sizeof(p6));
 p6.ni=reg;fault=mode;reads=queue_reads=active_queue=count=0;
 reg[0x234c/4]=seed;reg[0x23d8/4]=occupied;
 return rtl_fttr_p6_flush_queues(&p6);
}
void result(int *out)
{
 out[0]=p6.queue_completed;out[1]=p6.queue_failed;out[2]=p6.queue_error;
 out[3]=p6.queue_cleanup_error;out[4]=p6.queue_status;out[5]=reg[0x234c/4];
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
    originals = [trace(args.ni, cos, busy, seed) for cos, busy in [(0, 0), (7, 3), (3, 10000)]]
    records = []
    with tempfile.TemporaryDirectory(dir=root/'artifacts/fttr') as directory:
        directory = Path(directory)
        (directory/'queue.c').write_text(SHIM+'\n'+function(source,'p6_update')+'\n'+
            function(source,'rtl_fttr_p6_flush_queues')+'\n'+WRAPPER)
        subprocess.run(['cc','-O2','-shared','-fPIC','-Wall','-Wextra','-Werror','-I'+str(headers),
            str(directory/'queue.c'),'-o',str(directory/'queue.so')],check=True)
        native = ctypes.CDLL(str(directory/'queue.so'))
        native.run.argtypes = [ctypes.c_int,ctypes.c_uint,ctypes.c_uint]
        native.event.restype = ctypes.c_uint
        for case, fault, start, occupancy, expected in [
            ('empty',0,seed,0,0), ('drained',0,seed,0x00ff0000,0),
            ('stuck',1,seed,0x00ff0000,-errno.ETIMEDOUT),
            ('command-not-applied',2,seed,0x00ff0000,-errno.EIO),
            ('restore-not-applied',3,seed,0,-errno.EIO),
            ('stuck-and-restore-failed',5,seed,0x00ff0000,-errno.ETIMEDOUT),
            ('refilled',4,seed,0,-errno.EAGAIN),
            ('already-flushing',0,seed|0x8000,0,-errno.EBUSY)]:
            ret = native.run(fault,start,occupancy)
            state = (ctypes.c_int*6)();native.result(state)
            writes = [[native.event(i,0),native.event(i,1)] for i in range(native.event_count())]
            assert ret==expected,(case,ret,list(state))
            if fault not in (3,5):assert state[5]&0xffffffff==start
            if case in ('empty','drained'):
                assert state[0]==255 and state[4]&0xff0000==0
                for original in originals[:2]:
                    cos=original['cos'];assert writes[cos*2:cos*2+2]==original['writes']
                assert writes[-1]==[0x234c,seed]
            if case=='stuck':
                assert state[0]==7 and state[1]==3 and state[2]==-errno.ETIMEDOUT and state[3]==0
                assert state[4]&(1<<19) and originals[2]['last_status']&(1<<19)
                assert originals[2]['return']==0 and len(writes)==8
            if case=='restore-not-applied':assert state[2]==0 and state[3]==-errno.EIO
            if case=='stuck-and-restore-failed':assert state[2]==-errno.ETIMEDOUT and state[3]==-errno.EIO
            if case=='already-flushing':assert not writes
            records.append({'case':case,'return':ret,'completed':state[0],'failed_queue':state[1],
                'primary_error':state[2],'cleanup_error':state[3],'last_occupancy':state[4],'writes':len(writes)})
    report = {'native_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'stock_module_sha256':hashlib.sha256(args.ni.read_bytes()).hexdigest(),
        'original':originals,'native':records,
        'scope':'Production P6 ES-flush function versus actual stock ARM64. Occupancy/write completion and poll time are modeled. '
            'This does not verify DMA prefetch/PE/MiniOLT buffers or optical forwarding.'}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print('P6 ES: original false-success reproduced; native timeout, control restoration, readback failure and refill checks passed')


if __name__ == '__main__':
    main()
