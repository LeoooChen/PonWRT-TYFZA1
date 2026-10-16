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
#include "rtl9617c-gpon-hw.h"
#include "rtl9617c-xgpon-hw.h"
#include "rtl9617c-pon-itu-puc.h"
#include "rtl9617c-xgtc-recovery.h"
#include "rtl9617c-pon-path.h"
struct rtl9617c_pon {
 unsigned char *mac;
 unsigned int mode, itu_alloc_valid;
 unsigned int itu_alloc_ids[32], itu_gpon_gems[128];
 bool fe_enabled, xgpon_restore_pending;
 struct rtl_xgtc_puc_saved xgpon_puc_saved;
 unsigned int xgpon_restore_tcont[32];
};
static unsigned int registers[0x10000/4], upstream[256], downstream[4096], tcont[32];
static unsigned int fail_register;
static unsigned int schedules, burst_waits;
static void usleep_range(unsigned int min,unsigned int max) {
 assert(min==1000 && max==1100 && schedules==31);
 burst_waits++;
}
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
 if(reg==fail_register)return -ETIMEDOUT;
 if(reg==0x8000)schedules++;
 unsigned int *table=NULL,data=0,index=command&65535;
 switch(reg) {
 case 0x612c: table=tcont;index=index==1063?1:0;data=0x6130;break;
 case 0x6134: table=downstream;data=0x6138;break;
 case 0x6170: table=upstream;data=0x6174;break;
 case 0x4838: table=tcont;data=0x483c;break;
 case 0x4618: table=upstream;data=0x461c;break;
 }
 if(table) {
  if(command&BIT(30)) {
   unsigned int value=registers[data/4];
   /* sw_aes=0 leaves the PLOAM AES decision in the GPON table. */
   if(reg==0x6134 && !(command&BIT(16)))value=(value&~2U)|(table[index]&2U);
   table[index]=value;
  }
  else registers[data/4]=table[index];
 }
 return 0;
}
'''
cases = r'''
static int program(struct rtl9617c_pon *pon,const struct rtl_itu_paths *paths) {
 schedules=burst_waits=0;
 int ret=path_program(pon,paths);
 assert(burst_waits==(pon->mode!=RTL_PON_GPON));
 return ret;
}
int main(void) {
 for(unsigned int mode=0;mode<3;mode++)
 for(unsigned int direction=1;direction<=3;direction++) {
  bool gpon=mode==RTL_PON_GPON;
  memset(registers,0,sizeof(registers));memset(upstream,0,sizeof(upstream));
  memset(downstream,0,sizeof(downstream));memset(tcont,0,sizeof(tcont));
  if(gpon)downstream[4000]=2;
  registers[0x4004/4]=mode==1?0x61:0xa2;
  upstream[1]=0x10011;tcont[0]=0x11000011;
  struct rtl9617c_pon pon={.mac=(void *)registers,.mode=mode};
  struct rtl_itu_paths empty={0};
  assert(!program(&pon,&empty));
  struct rtl_itu_paths paths={.count=1,.path={{.gem_id=4000,.index=8,
   .alloc_id=direction&1?1063:65535,.tcont=direction&1?1:0,
   .pbits=direction&1?255:0,.downstream_pbits=direction&2?255:0}}};
  assert(!program(&pon,&paths));
  if(gpon) {
   assert((upstream[8]==4000)==!!(direction&1));
   assert((downstream[4000]&1)==!!(direction&2));
   assert(downstream[4000]&2);
   assert((tcont[1]&3)==(direction&1?3U:0U));
  } else {
   assert(!!(upstream[8]&BIT(16))==!!(direction&1));
   assert(!!(registers[(0x412c+8*4)/4]&BIT(17))==!!(direction&2));
   assert(!!(tcont[1]&BIT(24))==!!(direction&1));
   assert(pon.xgpon_restore_tcont[1]==(direction&1?(BIT(24)|1063):0));
  }
  assert(!!(registers[0x81bc/4]&0xff00)==!!(direction&1));
  if(!gpon && (direction&1)) {
   pon.xgpon_restore_pending=true;
   assert(!program(&pon,&paths));
   assert(registers[0x81bc/4]==255 && pon.xgpon_puc_saved.valid[0]==65535);
   assert(pon.xgpon_restore_tcont[1]==(BIT(24)|1063));
   pon.xgpon_restore_pending=false;
   assert(!program(&pon,&paths));
  }
  /* Disable business queues/GEM without revoking a PLOAM-owned allocation. */
  if(direction==3) {
   paths.path[0].tcont=0;paths.path[0].alloc_id=65535;paths.path[0].pbits=0;
   assert(!program(&pon,&paths));
   assert(gpon?upstream[8]==0:!(upstream[8]&BIT(16)));
   assert(!(registers[0x81bc/4]&0xff00));
   assert(gpon?!(tcont[1]&3):tcont[1]==0x11000427);
   if(!gpon)assert(!pon.xgpon_restore_tcont[1]);
   if(gpon)assert(downstream[4000]&2);
   else {
    tcont[1]&=~BIT(28); /* OLT has released its allocation. */
    assert(!program(&pon,&empty));
    assert(tcont[1]==(RTL_XGPON_ALLOC_ID_INVALID&0x3fff));
   }
  }
  if(!gpon) {
   assert(registers[0x4004/4]==(mode==1?0x61U:0xa2U));
   assert(upstream[1]==0x10011 && tcont[0]==0x11000011);
   fail_register=0x4838;
   assert(program(&pon,&paths)==-ETIMEDOUT);
   fail_register=0;
  }
 }
 puts("GTC/XGTC business tables: empty/add/replace, PLOAM ownership, OMCC retained; one burst wait after all business schedulers stop, no global flush; table timeout propagates");
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-gem-direction-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(fixture + source[start:end] + cases)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(drivers),
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
