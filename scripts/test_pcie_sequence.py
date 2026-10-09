#!/usr/bin/env python3
"""Compare native single-lane calibration with the fixed Taurus GPL sequence."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
reference = root / 'evidence/rtl-workspace/downloads/reference/tyfza1-port-study-20261005/linux-5.10.x/drivers/pci/controller/dwc/pcie-cortina.c'
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/pci/controller/dwc/rtl9617c-pcie-sequence.h'
original = reference.read_text()
def function(name):
    start = re.search(r'^static void ' + name + r'\(', original, re.M).start()
    end = original.index('{', start) + 1
    depth = 1
    while depth:
        depth += (original[end] == '{') - (original[end] == '}')
        end += 1
    return original[start:end]

calibration = function('serdes_phy_calibration')
# The remaining GPL tail resets generic PHY/core; this test compares analog
# register writes, while the native host owns the corresponding reset calls.
calibration = calibration[:calibration.index('// DPHY needs to reset')] + '}\n'
common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-pcie-sequence.h"
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) (((~0U)<<(l))&((~0U)>>(31-(h))))
#define FIELD_GET(mask,v) (((v)&(mask))>>__builtin_ctz(mask))
#define FIELD_PREP(mask,v) (((v)<<__builtin_ctz(mask))&(mask))
#define test_bit(bit,p) (((*(p))>>(bit))&1)
struct device {};
struct dw_pcie {struct device *dev;};
struct cortina_pcie {struct dw_pcie *pci;unsigned int idx,lanes;void *serdes_base;};
static u32 memory[2048];
struct write {unsigned int reg,value;};
static struct write trace[128];static unsigned int used;
static void writel(u32 value,void *address){
 unsigned int offset=(char *)address-(char *)memory;
 assert(!(offset&3) && offset<sizeof(memory));assert(used<128);
 trace[used++]=(struct write){offset,value};memory[offset/4]=value;
}
static u32 readl(void *address){return *(u32 *)address;}
#define usleep_range(a,b) ((void)0)
static unsigned int native_read(void *context,unsigned int reg){(void)context;return memory[reg/4];}
static void native_write(void *context,unsigned int reg,unsigned int value){(void)context;writel(value,(char *)memory+reg);}
static void native_delay(void *context){(void)context;}
static int native_wait(void *context,unsigned int reg,unsigned int mask){
 (void)context;return (memory[reg/4]&mask)==mask?0:-ETIMEDOUT;
}
static int atu_wait_clear(void *context,unsigned int reg,unsigned int mask){
 (void)context;assert(reg==0 && mask==BIT(31));
 assert(memory[reg/4]&mask);memory[reg/4]&=~mask;
 return 0;
}
'''
test = r'''
int main(void){
 struct dw_pcie dw={};struct cortina_pcie stock={.pci=&dw,.lanes=1,.serdes_base=memory};
 struct rtl_pcie_io io={NULL,native_read,native_write,native_delay,native_wait};
 struct write expected[128];u32 initial[2048];unsigned int cases=0;
 for(unsigned int profile=0;profile<2;profile++)
  for(unsigned int index=0;index<2;index++)
   for(unsigned int vector=0;vector<512;vector++) {
    for(unsigned int i=0;i<2048;i++)initial[i]=0xa55a1234U+vector*0x123457U+i*37;
    initial[0x7c/4]|=BIT(4);
    memcpy(memory,initial,sizeof(memory));used=0;stock.idx=index;
    if(!profile)serdes_phy_toggle(&stock);else serdes_phy_calibration(&stock);
    unsigned int expected_count=used;memcpy(expected,trace,used*sizeof(*trace));
    memcpy(memory,initial,sizeof(memory));used=0;
    assert(!rtl_pcie_serdes_calibrate(&io,index,profile?'B':'A'));
    assert(used==expected_count && !memcmp(trace,expected,used*sizeof(*trace)));
    cases++;
   }
 memset(memory,0,sizeof(memory));used=0;
 assert(rtl_pcie_serdes_calibrate(&io,0,'B')==-ETIMEDOUT);
 /* Literal Taurus ATU fixture:512MiB DDR,64KiB pages,CCI alias4. */
 struct rtl_pcie_io atu_io={NULL,native_read,native_write,NULL,atu_wait_clear};
 used=0;assert(!rtl_pcie_atu_ddr(&atu_io,0x2000,4));
 assert(memory[0]==1); /* Command completed after the hardware cleared busy. */
 assert(memory[1]==0 && memory[2]==0xffffe000 && memory[3]==0x40000);
 assert(memory[4]==0x07ff0022 && memory[5]==0x80 && memory[6]==0x80);
 puts("Native PCIe:2048 stock-calibration write traces match;analog timeout and512MiB ATU data fixture passed");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='pcie-sequence-') as directory:
    work = Path(directory)
    source, binary = work / 'sequence.c', work / 'sequence'
    source.write_text(common + function('serdes_phy_toggle') + calibration + test)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', '-I' + str(header.parent),
                    str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/pcie-sequence-test.json').write_text(json.dumps({
    'GPL_reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
    'native_header_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
    'stock_write_trace_vectors': 2048, 'profiles': ['A', 'B'], 'controllers': [0, 1],
    'lanes': 1, 'analog_timeout_reported': True, 'DDR_ATU_fixture_bytes': 536870912,
    'ATU_fixture_completion': 'mock hardware clears busy;production wait tested separately',
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False,
}, indent=2) + '\n')
