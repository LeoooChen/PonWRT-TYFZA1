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
struct work {unsigned int pending;};
struct rtl9617c_pon {
 int lock;enum rtl_pon_mode mode;unsigned char *mac;struct device *dev;
 struct net_device *omci_device;void *tx_disable,*top_irq;struct work gpon_recovery_work;
 bool gpon_recover;
 bool gpon_enabled,gpon_ds_fec,gpon_omcc_ready,itu_paths_ready;
 unsigned int gpon_last_state,gpon_omcc_onu_id,gpon_omcc_gem;
 u32 gpon_last_irq;u64 gpon_irq_count,gpon_key_switches,itu_line_epoch;
 int gpon_activation_error;
};
static unsigned int invalidated;
static u32 *mmio,tcont[4096],gem[256];
static struct rtl9617c_pon *observed;
static bool check_teardown;
static u32 readl(const void *p){return *(const u32*)p;}
static void writel(u32 v,void *p){
 uintptr_t reg=(uintptr_t)p-(uintptr_t)mmio;
 if(reg==0x608c){*(u32*)p&=~v;return;}
 *(u32*)p=v;
 if((reg==0x612c||reg==0x6170)&&(v&BIT(31))){
  if(check_teardown)assert(observed->itu_line_epoch==1 && !observed->itu_paths_ready && !observed->omci_device->carrier);
  u32 *table=reg==0x612c?tcont:gem;unsigned int index=v&(reg==0x612c?4095:255),data=reg==0x612c?0x6130:0x6174;
  if(v&BIT(30))table[index]=reg==0x612c?(mmio[data/4]&~1U)|(table[index]&1U):mmio[data/4];
  else mmio[data/4]=table[index];
  *(u32*)p=v&~BIT(31);
 }
}
static void rtl_pon_update(void *p,u32 mask,u32 value){writel((readl(p)&~mask)|value,p);}
static void schedule_delayed_work(struct work *w,unsigned int delay){assert(!delay);w->pending++;}
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
 u32 regs[0xc000/4]={},top[2]={0,1};struct net_device omci={.carrier=true};struct device dev={};int gpio=0;
 mmio=regs;
 struct rtl9617c_pon pon={.lock=1,.mode=RTL_PON_GPON,.mac=(void*)regs,.dev=&dev,
  .omci_device=&omci,.tx_disable=&gpio,.top_irq=top,.gpon_enabled=true,.gpon_omcc_ready=true,
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
 assert(pon.gpon_recover && pon.gpon_recovery_work.pending==1 && !(top[1]&1));
 regs[0x608c/4]=BIT(30);regs[0x60bc/4]=(4U<<16)|18;
 rtl_pon_gpon_irq(&pon);
 assert(regs[0x608c/4]==BIT(30) && pon.itu_line_epoch==1 && !omci.carrier);
 for(unsigned int next=17;next<=18;next++){
  memset(regs,0,sizeof(regs));memset(tcont,0,sizeof(tcont));memset(gem,0,sizeof(gem));
  top[1]=1;omci.carrier=true;invalidated=0;
  pon=(struct rtl9617c_pon){.lock=1,.mode=RTL_PON_GPON,.mac=(void*)regs,.dev=&dev,
   .omci_device=&omci,.tx_disable=&gpio,.top_irq=top,.gpon_enabled=true,.gpon_omcc_ready=true,
   .gpon_omcc_onu_id=17,.gpon_omcc_gem=1111,.gpon_last_state=4,.itu_paths_ready=true};
  tcont[17]=3;tcont[18]=1;observed=&pon;check_teardown=true;
  regs[0x608c/4]=regs[0x6090/4]=BIT(30)|BIT(31);regs[0x60bc/4]=(4U<<16)|next;regs[0x60c8/4]=BIT(12)|3333;
  rtl_pon_gpon_irq(&pon);check_teardown=false;
  assert(pon.itu_line_epoch==1 && invalidated==1 && !pon.itu_paths_ready && !omci.carrier);
  assert(pon.gpon_recover && pon.gpon_recovery_work.pending==1 && !(top[1]&1));
  assert(!pon.gpon_omcc_ready && pon.gpon_omcc_onu_id==17 && pon.gpon_omcc_gem==1111);
  for(unsigned int i=0;i<8;i++)assert(!gem[i]); /* New bindings wait for drain. */
 }
 for(unsigned int initial=0;initial<3;initial++){
  memset(regs,0,sizeof(regs));top[1]=1;invalidated=0;omci.carrier=!initial;
  pon=(struct rtl9617c_pon){.lock=1,.mode=RTL_PON_GPON,.mac=(void*)regs,.dev=&dev,
   .omci_device=&omci,.tx_disable=&gpio,.top_irq=top,.gpon_enabled=true,.gpon_omcc_ready=!initial,
   .gpon_omcc_onu_id=initial==1?255:17,.gpon_omcc_gem=1111,.gpon_last_state=initial?3:4,.itu_paths_ready=!initial};
  unsigned int current=initial==2?18:17;
  tcont[17]=3;tcont[18]=1;regs[0x608c/4]=regs[0x6090/4]=(initial==2?0:BIT(30))|(initial?BIT(31):0);
  regs[0x60bc/4]=(4U<<16)|current;regs[0x60c8/4]=BIT(12)|1111;
  rtl_pon_gpon_irq(&pon);
  assert(!pon.itu_line_epoch && !invalidated && !pon.gpon_recover && omci.carrier && pon.gpon_omcc_onu_id==current);
 }
 puts("Actual GPON IRQ: O6 grace, coalesced assignment/state teardown before new bindings; initial/repeated assignments and delayed O5 use the current identity");
}
'''
with tempfile.TemporaryDirectory(prefix='gpon-o6-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(model + ''.join(function(name) for name in (
        'rtl_gpon_read', 'rtl_gpon_write', 'rtl_gpon_indirect', 'rtl_gpon_omcc_assign',
        'rtl_gpon_omcc_map', 'rtl_pon_gpon_irq')) + cases)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(drivers), str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
