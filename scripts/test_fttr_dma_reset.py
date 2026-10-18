#!/usr/bin/env python3
"""Compare native shared-DMA shutdown with TYFZA1's DMA/AFT reset sequence."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE


def function(source, name):
    start = re.search(r'^.*\b'+name+r'\([^;]*?\)\n\{', source, re.M).start()
    end = source.index('{', start)+1
    depth = 1
    while depth:
        depth += (source[end]=='{')-(source[end]=='}')
        end += 1
    return source[start:end]


def original(binary, control, aft):
    events, calls = [], []
    boundaries = {'printk', 'ca_ni_tx_dump_debug_reg', 'ca_ni_reset_tx_ring',
        'ca_ni_tx_dma_lso_readwrite_ptr_reset', 'aal_ni_init_tx_dma_lso'}

    def boundary(name, args):
        assert name in boundaries, (name,args)
        if name!='printk':calls.append(name)
        return 0

    oracle = ModuleOracle(binary, ['ca_ni_dma_lso_reset'], boundary)
    base = 0x6000000
    oracle.uc.mem_map(base,0x10000)
    oracle.word(oracle.symbols['g_dma_reg_iobase'][0],base)
    oracle.uc.mem_write(base,struct.pack('<I',control))
    oracle.uc.mem_write(base+0xf00,struct.pack('<I',aft))
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE,
        lambda u,k,a,s,v,d:events.append([a-base,v]),begin=base,end=base+0xffff)
    oracle.call('ca_ni_dma_lso_reset')
    assert events==[[0,control|0x100],[0xf00,aft|0x80000000],
        [0,control&~0x100],[0xf00,aft&~0x80000000]],events
    assert calls==['ca_ni_tx_dump_debug_reg','ca_ni_reset_tx_ring',
        'ca_ni_tx_dma_lso_readwrite_ptr_reset','aal_ni_init_tx_dma_lso']
    return events


SHIM = r'''
#include <stdint.h>
#define __iomem
#define BIT(n) (1U << (n))
typedef uint32_t u32;
struct rtl_dma_tx { void *registers; unsigned int vp; };
static u32 reg[0x1000/4],events[16][2],count;
static u32 readl(void *p) { return *(u32 *)p; }
static void writel(u32 value,void *p) {
 events[count][0]=((u32 *)p-reg)*4;events[count++][1]=value;*(u32 *)p=value;
}
static void udelay(unsigned int us) { (void)us; }
'''

WRAPPER = r'''
void run(unsigned int control,unsigned int aft) {
 struct rtl_dma_tx tx={.registers=reg,.vp=0};
 reg[0]=control;reg[0xf00/4]=aft;count=0;
 rtl_dma_tx_stop(&tx);
}
unsigned int event(unsigned int i,unsigned int word) {return events[i][word];}
unsigned int event_count(void) {return count;}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ni',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = (root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-dma.c').read_text()
    definitions = '\n'.join(line for line in source.splitlines() if line.startswith('#define ') and 'MEMORY_BYTES' not in line)
    names = ['rtl_dma_vp','rtl_dma_tx_stop']
    if re.search(r'^static void rtl_dma_tx_reset\(',source,re.M):names.insert(1,'rtl_dma_tx_reset')
    records = []
    with tempfile.TemporaryDirectory(dir=root/'artifacts/fttr') as directory:
        directory = Path(directory)
        (directory/'reset.c').write_text(SHIM+'\n'+definitions+'\n'+'\n'.join(function(source,name) for name in names)+'\n'+WRAPPER)
        subprocess.run(['cc','-O2','-shared','-fPIC','-Wall','-Wextra','-Werror',str(directory/'reset.c'),'-o',str(directory/'reset.so')],check=True)
        native = ctypes.CDLL(str(directory/'reset.so'))
        native.run.argtypes=[ctypes.c_uint,ctypes.c_uint];native.event.restype=ctypes.c_uint
        for control,aft in [(0x1d,0x401),(0xa542003d,0x12345210)]:
            stock = original(args.ni,control&~1,aft)
            native.run(control,aft)
            writes=[[native.event(i,0),native.event(i,1)] for i in range(native.event_count())]
            assert writes==[[0x100,0],[0,control&~1]]+stock,('DMA/AFT reset differs',writes,stock)
            records.append({'initial_control':control,'initial_aft':aft,'stock_reset':stock,'native_stop':writes})
    result = {'stock_sha256':hashlib.sha256(args.ni.read_bytes()).hexdigest(),
        'native_source_sha256':hashlib.sha256(source.encode()).hexdigest(),'cases':records,
        'scope':'Native stop executes with MMIO modeled; original reset instructions execute with logging/ring rebuild/init callees modeled. '
            'Verifies dual reset ordering, not live prefetch/PE/MiniOLT retirement or per-ONU isolation.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print('Native DMA/AFT paired reset matches original instructions after VP/DMA submission is disabled')


if __name__=='__main__':main()
