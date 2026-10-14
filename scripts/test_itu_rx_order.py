#!/usr/bin/env python3
"""Run native PLOAM reads while the hardware refills the first released buffer."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-itu.c').read_text()


def function(name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
typedef uint32_t u32;
typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#include "rtl9617c-gpon-hw.h"
#include "rtl9617c-xgpon-hw.h"
enum {RTL_PON_EPON_D10G=1,RTL_PON_GPON=3,RTL_PON_XGPON=4,RTL_PON_XGSPON=5};
static bool rtl_pon_mode_is_epon(unsigned int mode){return mode<3;}
struct rtl9617c_pon {
 unsigned int mode;int control_lock,lock;uint64_t ploam_rx,ploam_tx,ploam_mic_errors;
 unsigned char xgpon_pending_ploam[48];u32 xgpon_pending_status;bool xgpon_pending_valid;
 unsigned char *mac;
};
static void mutex_lock(int *lock){assert(!*lock);*lock=1;}
static void mutex_unlock(int *lock){assert(*lock);*lock=0;}
static unsigned char registers[0x10000];
static bool refill;
static unsigned int readl(const void *address){unsigned int value;memcpy(&value,address,4);return value;}
static void writel(unsigned int value,void *address){
 memcpy(address,&value,4);
 if(address==registers+0x4b28 && !value && refill) {
  refill=false;
  for(unsigned int i=0;i<48;i+=4){value=0xeeeeeeee;memcpy(registers+0x4b80+i,&value,4);}
  value=BIT(31)|BIT(30);memcpy(registers+0x4b28,&value,4);
 }
}
'''
test = r'''
int main(void) {
 for(unsigned int mode=RTL_PON_XGPON;mode<=RTL_PON_XGSPON;mode++) {
  memset(registers,0,sizeof(registers));
  struct rtl9617c_pon pon={.mode=mode,.mac=registers};
  unsigned char data[48];
  for(unsigned int buffer=0;buffer<2;buffer++)
   for(unsigned int i=0;i<48;i+=4)writel(buffer?0x11111111:0x00000000,registers+0x4b80+48*buffer+i);
  writel(BIT(31)|BIT(30),registers+0x4b28);
  writel(BIT(31)|BIT(30),registers+0x4b2c);
  refill=true;
  assert(rtl_pon_ploam_receive(&pon,(char*)data,48)==48 && data[0]==0);
  assert(rtl_pon_ploam_receive(&pon,(char*)data,48)==48 && data[0]==0x11);
  assert(rtl_pon_ploam_receive(&pon,(char*)data,48)==48 && data[0]==0xee);
  assert(pon.ploam_rx==3 && !pon.xgpon_pending_valid);
  assert(rtl_pon_ploam_receive(&pon,(char*)data,48)==-EAGAIN);
  writel(BIT(31)|BIT(15),registers+0x4b28);
  assert(rtl_pon_ploam_receive(&pon,(char*)data,48)==-EBADMSG);
  assert(pon.ploam_rx==3 && pon.ploam_mic_errors==1);
  assert(!(readl(registers+0x4b28)&BIT(31)));
 }
 puts("XG-PON/XGS-PON native RX: old0,old1,refilled0 order preserved; MIC error recycled without delivery");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='itu-rx-order-') as work:
    path = Path(work) / 'order.c'
    binary = Path(work) / 'order'
    path.write_text(common + '\n'.join(function(name) for name in
                    ('rtl_itu_read', 'rtl_itu_write', 'rtl_pon_ploam_receive')) + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-I' + str(drivers), str(path), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
