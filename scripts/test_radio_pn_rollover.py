#!/usr/bin/env python3
"""Exercise the native DCTL PN read at a low16 carry during SRAM reads."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/security.h').read_text()
a = source.index('static inline u64 rtl_ax_security_read_pn(')
b = source.index('\n}\n', a) + 3
fixture = r"""
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
typedef uint32_t u32;typedef uint64_t u64;
struct rtl_ax_io {void *ctx;void(*write32)(void*,unsigned,u32);u32(*read32)(void*,unsigned);};
static unsigned macid,selector,advance,reads;
static u64 pn;
static void write32(void *ctx,unsigned reg,u32 value) {
 (void)ctx;assert(reg==0xc04);selector=value;
 assert(value==0x18800000+macid*32 || value==0x18800004+macid*32);
}
static u32 read32(void *ctx,unsigned reg) {
 (void)ctx;assert(reg==0x40000);reads++;
 if(selector==0x18800004+macid*32)return pn>>16;
 u32 value=(pn&65535)<<16;
 if(advance){pn++;advance=0;}
 return value;
}
"""
test = r"""
int main(void) {
 struct rtl_ax_io io={.write32=write32,.read32=read32};unsigned failures=0;
 const u64 vectors[]={0,1,0xffff,0x10000,0x123456abcdefULL,0xffffffffffffULL};
 for(macid=0;macid<128;macid++) {
  for(unsigned i=0;i<6;i++) {
   pn=vectors[i];advance=0;reads=0;
   assert(rtl_ax_security_read_pn(&io,macid)==pn);
  }
  pn=0xffff;advance=1;reads=0;
  u64 actual=rtl_ax_security_read_pn(&io,macid);
  if(actual!=pn){failures++;if(macid==0)printf("torn PN actual=%llx expected=%llx\n",(unsigned long long)actual,(unsigned long long)pn);}
  else if(macid==0)printf("carry read actual=%llx reads=%u\n",(unsigned long long)actual,reads);
 }
 printf("MACIDs=128 static_vectors=768 carry_failures=%u; SRAM reads modeled\n",failures);
 return failures?1:0;
}
"""
with tempfile.TemporaryDirectory(prefix='tyfza1-pn-carry-') as folder:
    folder=Path(folder);c=folder/'pn.c';binary=folder/'pn'
    c.write_text(fixture+source[a:b]+test)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',str(c),'-o',str(binary)],check=True)
    result=subprocess.run([str(binary)])
    raise SystemExit(result.returncode)
