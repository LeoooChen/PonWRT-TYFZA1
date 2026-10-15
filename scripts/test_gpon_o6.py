#!/usr/bin/env python3
"""Execute the production GPON IRQ for business state retained during O6."""
from pathlib import Path
import re, subprocess, tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-gpon.c').read_text()


def function(name):
    start = re.search(r'^(?:static )?(?:void|int|unsigned int) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-gpon-hw.h"
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define lockdep_assert_held(p) assert(*(p))
#define dev_info(...) ((void)0)
#define KOBJ_CHANGE 0
#define readl_poll_timeout(p,v,c,d,t) ({v=readl(p);(c)?0:-110;})
struct device {int kobj;};
struct net_device {bool carrier;};
struct rtl9617c_pon {
 int lock;enum rtl_pon_mode mode;unsigned char *mac;struct device *dev;
 struct net_device *omci_device;void *tx_disable;
 bool gpon_enabled,gpon_ds_fec,gpon_omcc_ready,itu_paths_ready;
 unsigned int gpon_last_state,gpon_omcc_onu_id,gpon_omcc_gem;
 u32 gpon_last_irq;u64 gpon_irq_count,gpon_key_switches,itu_line_epoch;
 int gpon_activation_error;
};
static unsigned int invalidated;
static u32 readl(const void *p){return *(const u32*)p;}
static void writel(u32 v,void *p){*(u32*)p=v;}
static void get_random_bytes(void *p,unsigned int n){memset(p,1,n);}
static void memzero_explicit(void *p,unsigned int n){memset(p,0,n);}
static void netif_carrier_on(struct net_device *p){p->carrier=true;}
static void netif_carrier_off(struct net_device *p){p->carrier=false;}
static void gpiod_set_value_cansleep(void *p,int value){*(int*)p=value;}
static void kobject_uevent_env(int *k,int event,char **env){(void)k;(void)event;assert(env[0]);}
static void rtl_pon_itu_carrier(struct rtl9617c_pon *p){(void)p;}
static int rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *p,enum rtl_pon_mode mode){
 assert(p->lock && p->mode==mode);p->itu_paths_ready=false;invalidated++;return 0;
}
'''
cases = r'''
int main(void){
 u32 regs[0xc000/4]={};struct net_device omci={.carrier=true};struct device dev={};int gpio=0;
 struct rtl9617c_pon pon={.lock=1,.mode=RTL_PON_GPON,.mac=(void*)regs,.dev=&dev,
  .omci_device=&omci,.tx_disable=&gpio,.gpon_enabled=true,.gpon_omcc_ready=true,
  .gpon_omcc_onu_id=17,.gpon_last_state=4,.itu_paths_ready=true};
 regs[0x608c/4]=regs[0x6090/4]=BIT(31);regs[0x60bc/4]=(5U<<16)|17;
 rtl_pon_gpon_irq(&pon);
 assert(!omci.carrier && pon.itu_paths_ready && !invalidated && !pon.itu_line_epoch);
 regs[0x60bc/4]=(4U<<16)|17;
 rtl_pon_gpon_irq(&pon);
 assert(omci.carrier && pon.itu_paths_ready && !invalidated && !pon.itu_line_epoch);
 regs[0x60bc/4]=(5U<<16)|17;pon.gpon_omcc_ready=false;
 rtl_pon_gpon_irq(&pon);
 assert(!omci.carrier && !pon.itu_paths_ready && invalidated==1);
 pon.itu_paths_ready=true;pon.gpon_omcc_ready=true;regs[0x60bc/4]=(1U<<16)|255;
 rtl_pon_gpon_irq(&pon);
 assert(!omci.carrier && !pon.itu_paths_ready && invalidated==2 && pon.itu_line_epoch==1);
 puts("Actual GPON IRQ: O5/O6 preserves business configuration; missing OMCC and O6 expiry invalidate it");
}
'''
with tempfile.TemporaryDirectory(prefix='gpon-o6-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(model + ''.join(function(name) for name in (
        'rtl_gpon_read', 'rtl_gpon_write', 'rtl_gpon_indirect', 'rtl_pon_gpon_irq')) + cases)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(drivers), str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
