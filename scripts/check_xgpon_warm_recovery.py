#!/usr/bin/env python3
"""Run the actual warm-reset controller and delayed O5 handoff in a register model."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-xgpon.c').read_text()


def function(name):
    start = re.search(r'^(?:static )?(?:int|void|unsigned int) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-xgpon-reset.h"
typedef uint32_t u32;typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#define GFP_KERNEL 0
#define HZ 100
#define lockdep_assert_held(p) assert(*(p))
#define spin_lock_bh(p) ((void)(p))
#define spin_unlock_bh(p) ((void)(p))
#define dev_err(dev,...) ((void)(dev),fprintf(stderr,__VA_ARGS__))
#define to_delayed_work(w) (w)
#define container_of(p,t,m) ((t*)((char*)(p)-offsetof(t,m)))
#define msecs_to_jiffies(n) ((n)/10)
#define time_after_eq(a,b) ((long)((a)-(b))>=0)
#define readl_poll_timeout(p,v,c,d,t) ({v=readl(p);(c)?0:-ETIMEDOUT;})
struct work_struct {unsigned int pending;};
struct rtl9617c_pon {
 int lock,control_lock,omci_key_lock,serdes_error,xgpon_activation_error;
 enum rtl_pon_mode mode;unsigned char *mac;
 void *control,*gpon_control,*serdes_init,*top_irq,*ni_dev,*dev,*omci_device,*tx_disable,*optical_status;
 bool xgpon_enabled,xgpon_omcc_ready,omci_master_key_valid,xgpon_key_ready;
 bool xgpon_watchdog_armed,xgpon_restore_pending,xgpon_recover,xgpon_pending_valid,xgpon_rogue_pending;
 bool mac_prepared,puc_configured,pdc_configured;
 unsigned int xgpon_key_valid,xgpon_key_state,xgpon_last_state,xgpon_health_state;
 unsigned int xgpon_health_grants,xgpon_health_matches,xgpon_health_no_grants,xgpon_reset_reason;
 unsigned long xgpon_health_at,xgpon_restore_at;
 unsigned long long itu_line_epoch,xgpon_mac_resets,xgpon_health_fec,xgpon_health_sn;
 struct rtl_xgtc_puc_saved xgpon_puc_saved;u32 xgpon_restore_tcont[32];
 u8 xgpon_serial[8],xgpon_registration[36];struct work_struct xgpon_health_work;
};
static u32 global_control,global_gpon,sds,top[2],optical,ni_enable;
static unsigned long jiffies;
static unsigned int phy_resets,delays,pdc_inits,puc_inits,carriers,invalidations;
static int gpio,init_error;static bool carrier;
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void *kvmalloc(size_t n,int flags){assert(flags==GFP_KERNEL);return malloc(n);}
#define kvfree free
static u32 get_random_u32(void){return 0x12345678;}
static void msleep(unsigned int n){assert(n==1);delays++;}
static void udelay(unsigned int n){assert(n==500);}
static void netif_carrier_off(void *p){(void)p;carrier=false;}
static void rtl_pon_itu_carrier(struct rtl9617c_pon *p){assert(p->lock);carriers++;}
static void rtl_pon_dying_gasp_stop(struct rtl9617c_pon *p){assert(p->lock);}
static void gpiod_set_value_cansleep(void *p,int v){*(int*)p=v;}
static int rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *p,enum rtl_pon_mode mode){assert(p->lock && mode==p->mode);invalidations++;return 0;}
static u32 rtl9617c_ni_pon_gate(void *dev,u32 value){(void)dev;u32 old=ni_enable;ni_enable=value;return old;}
static int rtl_pon_serdes_restart_locked(struct rtl9617c_pon *p){assert(p->lock && p->control_lock && delays%2==1);phy_resets++;return 0;}
static int rtl_pon_pdc_configure_locked(struct rtl9617c_pon *p){assert(!p->lock && p->control_lock && !ni_enable);pdc_inits++;p->pdc_configured=true;return init_error;}
static int rtl_pon_puc_configure_locked(struct rtl9617c_pon *p){assert(!p->lock && p->control_lock && pdc_inits==puc_inits+1 && !ni_enable);puc_inits++;p->puc_configured=true;return 0;}
static void schedule_delayed_work(struct work_struct *w,unsigned long n){assert(n==10);w->pending++;}
'''
# Reuse only the indirect-table hardware model, not the snapshot test cases.
tables = (root / 'scripts/check_xgpon_mac_snapshot.c').read_text()
tables = tables[tables.index('static unsigned int regs'):tables.index('int main(void)')]
io = r'''
static u32 readl(const void *p){uintptr_t at=(uintptr_t)p-(uintptr_t)regs;return at<sizeof(regs)?read_reg(NULL,at):*(const u32*)p;}
static void writel(u32 value,void *p){
 uintptr_t at=(uintptr_t)p-(uintptr_t)regs;
 if(at<sizeof(regs)){
  write_reg(NULL,at,value);
  if((value&BIT(31)) && (at==0x8000 || at==0x81c4 || at==0x81ec || at==0x80e8 || at==0x4618 || at==0x4838 || at==0x9020 || at==0x8060)){
   assert(!ni_enable);int ret=access_reg(NULL,at,value);if(!ret)regs[at/4]&=~BIT(31);
  }
 }else{
  *(u32*)p=value;
  if(p==&global_control && !(value&0x300)){
   assert(!ni_enable && !(regs[0x4004/4]&3));
   memset(regs,0,sizeof(regs));memset(upstream,0,sizeof(upstream));memset(tcont,0,sizeof(tcont));
   memset(pdc,0,sizeof(pdc));memset(pvt,0,sizeof(pvt));memset(cir,0,sizeof(cir));memset(pir,0,sizeof(pir));memset(bp,0,sizeof(bp));
  }
 }
}
static void rtl_pon_update(void *p,u32 mask,u32 v){writel((readl(p)&~mask)|v,p);}
'''
cases = r'''
int main(void){
 for(unsigned int mode=RTL_PON_XGPON;mode<=RTL_PON_XGSPON;mode++){
  memset(regs,0,sizeof(regs));memset(tcont,0,sizeof(tcont));memset(upstream,0,sizeof(upstream));
  random_count=phy_resets=delays=pdc_inits=puc_inits=0;access_error=init_error=0;
  global_control=0x30e;global_gpon=mode==RTL_PON_XGPON?0x205:0x305;sds=BIT(4);ni_enable=0xa53f;
  struct rtl9617c_pon p={.lock=1,.control_lock=1,.mode=mode,.mac=(void*)regs,
   .control=&global_control,.gpon_control=&global_gpon,.serdes_init=&sds,.top_irq=top,
   .tx_disable=&gpio,.optical_status=&optical,.xgpon_enabled=true,.xgpon_key_ready=true};
  regs[0x4004/4]=mode==RTL_PON_XGPON?0x41:0x82;regs[0x4600/4]=BIT(2);regs[0x4d0c/4]=123;regs[0x4d10/4]=456;
  regs[0x81bc/4]=0xffff;tcont[1]=BIT(24)|1063;upstream[8]=BIT(16)|4000;regs[(0x412c+8*4)/4]=BIT(17)|4000;
  p.xgpon_restore_tcont[1]=BIT(24)|1063; /* Last successful business configuration. */
  pdc[8][0]=0x12345678;pdc[8][1]=0x1234;carrier=true;
  assert(!rtl_xgpon_mac_restart_locked(&p,true));
  assert(p.xgpon_enabled && !p.xgpon_key_ready && !carrier && p.xgpon_restore_pending && p.xgpon_mac_resets==1);
  assert(phy_resets==1 && delays==2 && pdc_inits==1 && puc_inits==1 && ni_enable==0xa53f);
  assert(regs[0x4004/4]==(mode==RTL_PON_XGPON?0x41U:0x82U) && sds==BIT(4));
  assert(regs[0x4d0c/4]==123 && regs[0x4d10/4]==456 && regs[0x81bc/4]==255);
  assert(upstream[8]==(BIT(16)|4000) && regs[(0x412c+8*4)/4]==(BIT(17)|4000));
  assert(pdc[8][0]==0x12345678 && pdc[8][1]==0x1234 && !tcont[1]);
  /* A second reset before O5 must retain the assigned OMCI Alloc-ID. */
  random_count=0;assert(!rtl_xgpon_mac_restart_locked(&p,false));
  assert(p.xgpon_restore_tcont[1]==(BIT(24)|1063) && !tcont[1]);
  /* ACTP is hardware-driven. O5 restores OMCI ownership, then waits3000ms. */
  p.lock=p.control_lock=0;p.xgpon_health_at=999999;jiffies=100;ni_enable=0;
  regs[0x4d30/4]=3;rtl_pon_xgpon_health(&p.xgpon_health_work);
  assert((tcont[1]&0x1fffffff)==(BIT(24)|1063) && p.xgpon_restore_at==400 && regs[0x81bc/4]==255);
  jiffies=399;rtl_pon_xgpon_health(&p.xgpon_health_work);assert(p.xgpon_restore_pending);
  jiffies=400;rtl_pon_xgpon_health(&p.xgpon_health_work);assert(!p.xgpon_restore_pending && regs[0x81bc/4]==0xffff);
  /* A persistent PLOAM busy bit requests MAC-only recovery; no PHY restart. */
  ni_enable=0xa53f;p.xgpon_watchdog_armed=true;regs[0x4afc/4]=BIT(31);random_count=0;
  rtl_pon_xgpon_health(&p.xgpon_health_work);
  assert(p.xgpon_reset_reason==5 && p.xgpon_mac_resets==3 && phy_resets==1 && !p.xgpon_activation_error && p.xgpon_watchdog_armed);
  /* A real initialization failure stops activation and preserves its error. */
  regs[0x4afc/4]=BIT(31);init_error=-EIO;random_count=0;
  rtl_pon_xgpon_health(&p.xgpon_health_work);
  assert(!p.xgpon_enabled && !p.mac_prepared && p.xgpon_activation_error==-EIO && gpio==1 && !(sds&BIT(4)));
  assert(ni_enable==0xa53f && !p.lock && !p.control_lock);
  p.xgpon_enabled=true;p.xgpon_watchdog_armed=false;p.xgpon_rogue_pending=true;
  regs[0x4d30/4]=3;rtl_pon_xgpon_health(&p.xgpon_health_work);
  assert(!p.xgpon_rogue_pending && (regs[0x4d30/4]&7)==5 && !(regs[0x4d00/4]&1));
  assert(gpio==1 && (regs[0x4004/4]&3)==(mode==RTL_PON_XGPON?1U:2U) && phy_resets==2);
 }
 puts("Warm recovery: MAC/PHY ordering, GEM/PDC retention, O5 T-CONT ownership,3000ms VOQ handoff, PLOAM MAC-only reset, failed-init stop and deferred rogue force-O7 passed");
}
'''
data = model + tables + io + ''.join(function(n) for n in (
    'rtl_xgpon_read', 'rtl_xgpon_write', 'rtl_xgpon_indirect',
    'rtl_xgpon_mac_restart_locked', 'rtl_xgpon_health_check', 'rtl_pon_xgpon_health')) + cases
with tempfile.TemporaryDirectory(prefix='xgpon-warm-recovery-', dir=root / 'artifacts') as temp:
    c, binary = Path(temp) / 'check.c', Path(temp) / 'check'
    c.write_text(data)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(drivers), str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
