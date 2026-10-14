#!/usr/bin/env python3
"""Execute the production ITU table programmer for all three GEM directions."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-path.c').read_text()
start = re.search(r'^static int path_program\(', source, re.M).start()
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1

fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U >> (31-(h))) & (~0U << (l)))
#define RTL_PON_GPON 0
#define for_each_set_bit(i,b,n) for(i=0;i<n;i++) if((b)[i/32] & BIT(i%32))
#define __set_bit(i,b) ((b)[(i)/32] |= BIT((i)%32))
#define __clear_bit(i,b) ((b)[(i)/32] &= ~BIT((i)%32))
#define readl_poll_timeout(a,v,c,d,t) ({ v=readl(a)&~BIT(31); (c)?0:-1; })
#include "rtl9617c-gpon-hw.h"
#include "rtl9617c-xgpon-hw.h"
#include "rtl9617c-pon-itu-puc.h"
#include "rtl9617c-pon-path.h"
struct rtl9617c_pon {
 unsigned char *mac;
 unsigned int mode, itu_alloc_valid;
 unsigned int itu_alloc_ids[32], itu_gpon_gems[128];
 bool fe_enabled;
};
static unsigned int registers[0x10000/4], upstream[256], downstream[4096], tcont[32];
static unsigned int readl(const void *address) { return *(const unsigned int *)address; }
static void writel(unsigned int value, void *address) { *(unsigned int *)address=value; }
static unsigned int path_read(void *context,unsigned int reg) {
 return readl(((struct rtl9617c_pon *)context)->mac+reg);
}
static void path_write(void *context,unsigned int reg,unsigned int value) {
 writel(value,((struct rtl9617c_pon *)context)->mac+reg);
}
static int path_access(void *context,unsigned int reg,unsigned int command) {
 (void)context;
 unsigned int *table=NULL,data=0,index=command&65535;
 switch(reg) {
 case 0x612c: table=tcont;index=index==1063?1:0;data=0x6130;break;
 case 0x6134: table=downstream;data=0x6138;break;
 case 0x6170: table=upstream;data=0x6174;break;
 case 0x4838: table=tcont;data=0x483c;break;
 case 0x4618: table=upstream;data=0x461c;break;
 }
 if(table) {
  if(command&BIT(30))table[index]=registers[data/4];
  else registers[data/4]=table[index];
 }
 return 0;
}
static void rtl_pon_update(void *address,unsigned int mask,unsigned int value) {
 writel((readl(address)&~mask)|value,address);
}
'''
cases = r'''
int main(void) {
 for(unsigned int gpon=0;gpon<2;gpon++)
 for(unsigned int direction=1;direction<=3;direction++) {
  memset(registers,0,sizeof(registers));memset(upstream,0,sizeof(upstream));
  memset(downstream,0,sizeof(downstream));memset(tcont,0,sizeof(tcont));
  struct rtl9617c_pon pon={.mac=(void *)registers,.mode=gpon?RTL_PON_GPON:1};
  struct rtl_itu_paths paths={.count=1,.path={{.gem_id=4000,.index=8,
   .alloc_id=direction&1?1063:65535,.tcont=direction&1?1:0,
   .pbits=direction&1?255:0,.downstream_pbits=direction&2?255:0}}};
  assert(!path_program(&pon,&paths));
  if(gpon) {
   assert((upstream[8]==4000)==!!(direction&1));
   assert((downstream[4000]&1)==!!(direction&2));
   assert((tcont[1]&3)==(direction&1?3U:0U));
  } else {
   assert(!!(upstream[8]&BIT(16))==!!(direction&1));
   assert(!!(registers[(0x412c+8*4)/4]&BIT(17))==!!(direction&2));
   assert(!!(tcont[1]&BIT(24))==!!(direction&1));
  }
  assert(!!(registers[0x81bc/4]&0xff00)==!!(direction&1));
  /* Replacing a bidirectional GEM with receive-only must release its T-CONT. */
  if(direction==3) {
   paths.path[0].tcont=0;paths.path[0].alloc_id=65535;paths.path[0].pbits=0;
   assert(!path_program(&pon,&paths));
   assert(gpon?upstream[8]==0:!(upstream[8]&BIT(16)));
   assert(!(registers[0x81bc/4]&0xff00));
   assert(gpon?!(tcont[1]&3):!(tcont[1]&BIT(24)));
  }
 }
 puts("GTC/XGTC GEM directions: real table IO, downstream-only without T-CONT, and live direction replacement passed");
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-gem-direction-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(fixture + source[start:end] + cases)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(drivers),
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
