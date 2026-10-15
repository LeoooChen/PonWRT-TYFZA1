#!/usr/bin/env python3
"""A failed NI teardown must retain ownership until a PON mode switch can finish."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-pdc.c').read_text()
start = source.index('int rtl_pon_fe_configure_locked(')
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
function = source[start:end]
model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-pon-mode.h"
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
struct rtl9617c_pon {int mode,lock,control_lock;bool fe_enabled,pdc_configured,puc_configured,traffic_enabled;void *ni_dev,*mac;unsigned char pdc_source[64];};
static int failure,disables;
static void mutex_lock(int *lock){assert(!*lock);*lock=1;}
static void mutex_unlock(int *lock){assert(*lock);*lock=0;}
#define lockdep_assert_held(lock) assert(*(lock))
static int rtl9617c_ni_fe_enable(void *dev){(void)dev;return 0;}
static int rtl9617c_ni_fe_disable(void *dev){(void)dev;disables++;return failure;}
static int rtl_pdc_access(struct rtl9617c_pon *p,unsigned int entry,bool write){(void)p;(void)entry;(void)write;return -EIO;}
static void writel(u32 v,void *p){(void)v;(void)p;}
static u32 readl(void *p){(void)p;return 0;}
'''
test = r'''
int main(void){
 for(int mode=RTL_PON_EPON_1G;mode<RTL_PON_MODE_COUNT;mode++){
  struct rtl9617c_pon pon={.mode=mode,.control_lock=1,.fe_enabled=true};
  failure=-ETIMEDOUT;disables=0;
  assert(rtl_pon_fe_configure_locked(&pon,false)==-ETIMEDOUT);
  assert(pon.fe_enabled && disables==1 && !pon.lock);
  failure=0;
  assert(rtl_pon_fe_configure_locked(&pon,false)==0);
  assert(!pon.fe_enabled && disables==2 && !pon.lock);
  unsigned char mac[0xc000]={};pon.mac=mac;
  memset(pon.pdc_source,32,sizeof(pon.pdc_source));
  pon.pdc_configured=pon.puc_configured=pon.traffic_enabled=true;
  assert(rtl_pon_fe_configure_locked(&pon,true)==-EIO);
  assert(pon.fe_enabled && !pon.lock);
  pon.pdc_configured=false;
  assert(rtl_pon_fe_configure_locked(&pon,false)==0 && !pon.fe_enabled && disables==3);
 }
 puts("Actual PON FE ownership: all six modes retain NI after PDC setup failure or NI cleanup failure and release it only on successful cleanup");
}
'''
constants = '\n'.join(re.findall(r'^#define .*$', source, re.M))
with tempfile.TemporaryDirectory(prefix='pon-fe-stop-', dir=root / 'artifacts') as directory:
    directory = Path(directory)
    cfile = directory / 'test.c'
    binary = directory / 'test'
    cfile.write_text(model + constants + '\n' + function + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(drivers), str(cfile), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
