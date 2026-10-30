#!/usr/bin/env python3
"""Check the actual native start sequence against the captured factory writes.
This checks initialization completeness and ordering, not physical recovery.
"""
from pathlib import Path
import argparse
import hashlib
import sys
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--before", action="store_true")
parser.add_argument("--stock", type=Path, help="execute the original board module initialization")
args = parser.parse_args()
drivers = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
source = (drivers / "rtl9617c-dma.c").read_text()
if args.before:
    source = subprocess.check_output(["git", "show", "8119f3c:" + str((drivers / "rtl9617c-dma.c").relative_to(root))], cwd=root, text=True)
header = (drivers / "rtl9617c-dma.h").read_text()
# TYFZA1 ca-ne.ko aal_ni_init_tx_dma_lso programs DMA read IDs 0..4.
# The DMA window starts 0x1000 beyond the factory QM reorder base.
if args.stock:
    assert hashlib.sha256(args.stock.read_bytes()).hexdigest() == "16840ba70afc0ae700c2d63c57b8248475a67154eef89536d90028d4fe6486d7"
    sys.path.insert(0, str(root / "tools"))
    from arm64_module_oracle import ModuleOracle
    from unicorn import UC_HOOK_MEM_WRITE

    def boundary(name, values):
        if name == "printk":
            return 0
        raise AssertionError((name, values))

    oracle = ModuleOracle(args.stock,
        ["aal_ni_init_tx_dma_lso", "rtk_ni_init_tx_dma_lso"], boundary)
    mmio, reo = 0x6000000, 0x6100000
    oracle.uc.mem_map(mmio, 0x1000)
    oracle.uc.mem_map(reo, 0x3000)
    oracle.word(oracle.symbols["g_dma_reg_iobase"][0], mmio)
    oracle.word(oracle.symbols["g_ne_axi_reo_reg_iobase"][0], reo)
    writes = []

    def write(uc, access, address, size, value, data):
        if reo <= address < reo + 0x3000:
            writes.append((address - reo, size, value))

    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, write)
    oracle.call("aal_ni_init_tx_dma_lso", instruction_budget=100000)
    assert writes == [(0x1000 + i * 0x80, 4, 15) for i in range(5)], writes
    print("stock: all five DMA read-reorder IDs set to 15", flush=True)



def function(text, name):
    start = re.search(r"^.*\b" + name + r"\([^;]*?\)\s*\{", text, re.M).start()
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + "\n"


common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32,__le32;typedef uint64_t u64;typedef uintptr_t dma_addr_t;
#define __iomem
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define lower_32_bits(v) ((u32)(v))
static u32 dma[1024],reo[1024];
static bool fail_access;
static unsigned reo_writes,reo_reads;
static void writel(u32 v,void *p){
 uintptr_t a=(uintptr_t)p;
 if(a>=(uintptr_t)reo&&a<(uintptr_t)(reo+1024)){
  unsigned off=a-(uintptr_t)reo;
  assert(!(dma[0]&1));assert(off<=0x200&&off%0x80==0&&v==15);reo_writes++;
 }else assert(a>=(uintptr_t)dma&&a<(uintptr_t)(dma+1024));
 memcpy(p,&v,4);
}
static u32 readl(void *p){
 if(p==reo+0x200/4)reo_reads++;
 if(p==dma+(0x100+8*0xa0+4)/4&&!fail_access)*(u32 *)p&=~BIT(31);
 return *(u32 *)p;
}
static void udelay(unsigned n){assert(n==10);}
#define readl_poll_timeout(a,v,c,d,t) ({(void)(d);(void)(t);v=readl(a);(c)?0:-ETIMEDOUT;})
'''
types = header[header.index("#define RTL_DMA_DEPTH"):header.index("/* HP=11")]
defines = "\n".join(re.findall(r"^#define .+$", source, re.M)) + "\n"
cases = r'''
int main(void){
 for(unsigned pass=0;pass<16;pass++){
  struct rtl_dma_tx tx={.vp=8,.port=0,.registers=dma,.axi_reorder=reo,.memory_dma=0x04000000};
  tx.memory=malloc(MEMORY_BYTES);assert(tx.memory);memset(tx.memory,0xaa,MEMORY_BYTES);
  memset(dma,0,sizeof(dma));for(unsigned i=0;i<1024;i++)reo[i]=pass;
  dma[0]=0x1d;reo_writes=reo_reads=0;fail_access=false;
  assert(!rtl_dma_tx_start(&tx));assert(dma[0]&1);
#ifdef EXPECT_BEFORE
  assert(!reo_writes&&!reo_reads);
  for(unsigned i=0;i<1024;i++)assert(reo[i]==pass);
#else
  assert(reo_writes==5&&reo_reads==1);
  for(unsigned i=0;i<1024;i++)assert(reo[i]==(i<=128&&i%32==0?15:pass));
#endif
  for(unsigned i=0;i<MEMORY_BYTES;i++)assert(!((unsigned char *)tx.memory)[i]);
  /* A failed queue-base transaction never enables DMA or changes the bridge. */
  fail_access=true;reo_writes=reo_reads=0;
  assert(rtl_dma_tx_start(&tx)==-ETIMEDOUT);assert(!(dma[0]&1)&&!reo_writes);
  free(tx.memory);
 }
#ifdef EXPECT_BEFORE
 puts("parent: all five DMA reorder registers remain inherited; no initialization");
#else
 puts("native: 16 initial states match factory writes before DMA enable; other registers preserved; queue-base timeout leaves DMA disabled");
#endif
}
'''
data = common + types + defines + function(source, "rtl_dma_vp") + function(source, "rtl_dma_tx_start") + cases
with tempfile.TemporaryDirectory(prefix="ni-dma-reorder-") as directory:
    work = Path(directory)
    (work / "test.c").write_text(data)
    cmd = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-g"]
    if args.before:
        cmd += ["-DEXPECT_BEFORE"]
    subprocess.run(cmd + [str(work / "test.c"), "-o", str(work / "test")], check=True)
    subprocess.run([str(work / "test")], check=True)
