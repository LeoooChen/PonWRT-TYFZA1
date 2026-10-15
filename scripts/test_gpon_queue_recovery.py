#!/usr/bin/env python3
"""Execute production GPON drain/worker/stop with partial packets and timeouts."""
from pathlib import Path
import re, subprocess, tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-gpon.c').read_text()
header = (drivers / 'rtl9617c-pon.h').read_text()
state = header[header.index('enum rtl_gpon_drain_phase'):header.index('struct rtl9617c_pon {')]


def function(text, name):
    start = re.search(r'^(?:static )?(?:void|int|unsigned int) ' + name + r'\(', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-gpon-hw.h"
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h)))&(~0U<<(l)))
#define HZ 100
#define GFP_KERNEL 0
#define NETDEV_TX_BUSY 1
#define readl_poll_timeout(p,v,c,d,t) ({v=readl(p);(c)?0:-ETIMEDOUT;})
#define lockdep_assert_held(p) assert(*(p))
#define to_delayed_work(w) (w)
#define container_of(p,t,m) ((t*)((char*)(p)-offsetof(t,m)))
struct work_struct {unsigned int pending;};
struct net_device {bool carrier;};
struct sk_buff {unsigned int len;u8 data[64];};
struct rtl9617c_ni_frame {u32 header_high,header_low;};
'''
model += state + r'''
struct rtl9617c_pon {
 int lock,control_lock,mpcp_irq;enum rtl_pon_mode mode;
 unsigned char *mac;void *control,*gpon_control,*top_irq,*ni_dev,*tx_disable,*serdes_init;
 struct net_device *omci_device;struct work_struct gpon_recovery_work;
 bool gpon_recover,gpon_enabled,gpon_omcc_ready,mac_prepared,puc_configured,pdc_configured,fe_enabled;
 unsigned int gpon_omcc_onu_id,gpon_omcc_gem,itu_alloc_valid;u16 itu_alloc_ids[32];u64 itu_line_epoch;
 int gpon_activation_error;struct rtl_gpon_drain gpon_drain;unsigned int xgpon_recovery_phase;
};
static u32 regs[0xc000/4],pvt[32][5],alloc_table[4096],gem_table[256],bytes[256],packets[256];
static u32 top[2],control,gpon_control;
static unsigned int probes,flushes,gtc_flushes,resets,allocations,frees,public_inits;
static bool busy_flush,retain_packets,probe_busy,busy_omcc,busy_invalidate;
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void msleep(unsigned int n){assert(n==1);}
static void udelay(unsigned int n){assert(n==125||n==250);}
static void schedule_delayed_work(struct work_struct *w,unsigned int delay){assert(delay==HZ);w->pending++;}
static void cancel_delayed_work(struct work_struct *w){w->pending=0;}
static void synchronize_irq(int irq){(void)irq;}
static u32 readl(const void *p){return *(const u32*)p;}
static void writel(u32 value,void *p){
 *(u32*)p=value;
 uintptr_t reg=(uintptr_t)p-(uintptr_t)regs;
 if(reg==0x8000 && (value&BIT(31))){
  unsigned int index=value&255;
  for(unsigned int i=0;i<5;i++){
   if(value&BIT(30))pvt[index][i]=regs[(0x8014-i*4)/4];
   else regs[(0x8014-i*4)/4]=pvt[index][i];
  }
  *(u32*)p=value&~BIT(31);
 }else if(reg==0x612c && (value&BIT(31))){
  unsigned int index=value&4095;
  if(value&BIT(30))alloc_table[index]=(regs[0x6130/4]&~1U)|(alloc_table[index]&1U);
  else regs[0x6130/4]=alloc_table[index];
  *(u32*)p=value&~BIT(31);
 }else if(reg==0x6170 && (value&BIT(31))){
  unsigned int index=value&255;
  if(busy_omcc)return;
  if(value&BIT(30))gem_table[index]=regs[0x6174/4];else regs[0x6174/4]=gem_table[index];
  *(u32*)p=value&~BIT(31);
 }else if(reg==0x802c && (value&BIT(31))){
  unsigned int index=value&255;
  regs[0x8034/4]=bytes[index]|((packets[index]&2047)<<21);regs[0x8030/4]=packets[index]>>11;
  *(u32*)p=value&~BIT(31);
 }else if(reg==0x80dc && (value&BIT(31))){
  flushes++;
  if(!busy_flush){*(u32*)p=value&~BIT(31);if(!retain_packets)bytes[value&255]=packets[value&255]=0;}
 }else if(reg==0x61c4 && (value&BIT(31)))gtc_flushes++;
 else if(reg==0x6114 && (value&BIT(4)))resets++;
}
static void rtl_pon_update(void *p,u32 mask,u32 value){writel((readl(p)&~mask)|value,p);}
static void netif_carrier_off(struct net_device *p){p->carrier=false;}
static void netif_carrier_on(struct net_device *p){p->carrier=true;}
static void rtl_pon_itu_carrier(struct rtl9617c_pon *p){assert(p->lock);}
static bool netif_running(struct net_device *p){(void)p;return true;}
static void gpiod_set_value_cansleep(void *p,int value){*(int*)p=value;}
static int rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *p,enum rtl_pon_mode mode){assert(p->lock && p->mode==mode);if(busy_invalidate){assert(!regs[0x81bc/4]);return -EIO;}return 0;}
static int rtl_pon_puc_configure_locked(struct rtl9617c_pon *p){assert(p->control_lock);public_inits++;return 0;}
static int rtl_pon_pdc_configure_locked(struct rtl9617c_pon *p){assert(p->control_lock);public_inits++;return 0;}
static struct sk_buff *alloc_skb(unsigned int n,int flags){assert(n==64 && !flags);allocations++;return calloc(1,sizeof(struct sk_buff));}
static void *skb_put(struct sk_buff *p,unsigned int n){void *out=p->data+p->len;p->len+=n;assert(p->len<=64);return out;}
static void dev_kfree_skb_any(struct sk_buff *p){frees++;free(p);}
static int rtl9617c_ni_transmit(void *ni,struct net_device *dev,struct sk_buff *skb,const struct rtl9617c_ni_frame *frame){
 (void)ni;(void)dev;
 assert(skb->len==64 && frame->header_high==0x00202000 && frame->header_low==0x2000230b);
 static const u8 prefix[16]={0,1,2,3,4,5,0,0x11,0x22,0x33,0x44,0x55,0x81,0,1,0x40};
 assert(!memcmp(skb->data,prefix,16));for(unsigned int i=16;i<64;i++)assert(!skb->data[i]);
 assert(regs[0x81bc/4]==BIT(11));
 if(probe_busy)return NETDEV_TX_BUSY;
 probes++;bytes[11]+=64;packets[11]++;dev_kfree_skb_any(skb);return 0;
}
'''
cases = r'''
int main(void){
 for(unsigned int test=0;test<9;test++){
  memset(regs,0,sizeof(regs));memset(pvt,0,sizeof(pvt));memset(alloc_table,0,sizeof(alloc_table));
  memset(bytes,0,sizeof(bytes));memset(packets,0,sizeof(packets));memset(gem_table,0,sizeof(gem_table));
  probes=flushes=gtc_flushes=resets=allocations=frees=public_inits=0;
  busy_flush=test==3||test==5;retain_packets=test==4;probe_busy=test==2;busy_omcc=test==7;busy_invalidate=test==8;
  top[0]=top[1]=0;control=0x30e;gpon_control=3;int gpio=0;struct net_device omci={};
  struct rtl9617c_pon pon={.mode=RTL_PON_GPON,.mac=(void*)regs,.control=&control,.gpon_control=&gpon_control,
   .top_irq=top,.tx_disable=&gpio,.omci_device=&omci,.gpon_recover=true,.gpon_enabled=true,
   .mac_prepared=true,.puc_configured=true,.pdc_configured=true,.fe_enabled=true,.gpon_omcc_onu_id=17,.itu_alloc_valid=2};
  pon.itu_alloc_ids[1]=1063;alloc_table[17]=3;alloc_table[1063]=7;
  if(test==1||test==7){regs[0x60bc/4]=(4U<<16)|18;regs[0x60c8/4]=BIT(12)|3333;alloc_table[18]=1;}
  regs[0x808c/4]=0x20030002;regs[0x80e0/4]=0x12000006;regs[0x61c4/4]=0x05020000;regs[0x6114/4]=0x460062;
  regs[0x81bc/4]=65535;for(unsigned int i=0;i<16;i++)regs[(0x8090+i*4)/4]=0x55555555;
  for(unsigned int port=0;port<32;port++){
   for(unsigned int q=0;q<8;q++){
    u64 value=(port*8+q)|256;unsigned int bit=q*9;
    pvt[port][bit/32]|=value<<(bit%32);
    if(bit%32>23)pvt[port][bit/32+1]|=value>>(32-bit%32);
   }
   pvt[port][2]|=BIT(12);pvt[port][3]=0x12345678;pvt[port][4]=0x3210;
  }
  u32 saved_pvt[32][5];memcpy(saved_pvt,pvt,sizeof(pvt));
  if(test){bytes[11]=160;packets[11]=(test==1||test>=3)?2:0;}
  if(test==6){
   mutex_lock(&pon.control_lock);rtl_pon_gpon_stop_locked(&pon);mutex_unlock(&pon.control_lock);
   assert(!pon.gpon_recover && !pon.gpon_drain.phase && !pon.mac_prepared && !pon.puc_configured && !pon.pdc_configured);
   assert(!gpon_control && !(control&(BIT(8)|BIT(9))) && gpio==1 && !top[1] && !gtc_flushes);
   continue;
  }
  rtl_pon_gpon_recovery(&pon.gpon_recovery_work);
  if(test==8){
   assert(pon.gpon_recover && pon.gpon_drain.phase==RTL_GPON_DRAIN_INVALIDATE && pon.gpon_activation_error==-EIO);
   assert(!top[1] && !regs[0x81bc/4] && !resets && !gtc_flushes && !flushes && pon.gpon_drain.valid[0]==65535);
   assert(rtl_pon_puc_configure(&pon)==-EBUSY && rtl_pon_pdc_configure(&pon)==-EBUSY && !public_inits);
   busy_invalidate=false;rtl_pon_gpon_recovery(&pon.gpon_recovery_work);
  }else if(test==7){
   assert(pon.gpon_recover && pon.gpon_drain.phase==RTL_GPON_DRAIN_OMCC && pon.gpon_activation_error==-ETIMEDOUT);
   assert(!top[1] && !omci.carrier && resets==1 && gtc_flushes==32 && flushes==1 && regs[0x81bc/4]==65535);
   assert(rtl_pon_puc_configure(&pon)==-EBUSY && rtl_pon_pdc_configure(&pon)==-EBUSY && !public_inits);
   busy_omcc=false;regs[0x6170/4]&=~BIT(31);
   rtl_pon_gpon_recovery(&pon.gpon_recovery_work);
   assert(resets==1 && gtc_flushes==32 && flushes==1); /* No second drain. */
  }else if(test>=2){
   assert(pon.gpon_recover && pon.gpon_drain.phase && !top[1] && !resets);
   assert(pon.gpon_activation_error==(test==2?-EAGAIN:test==4?-EIO:-ETIMEDOUT));
   assert(!regs[0x81bc/4] && (regs[0x808c/4]&BIT(30)));
   assert(rtl_pon_puc_configure(&pon)==-EBUSY && rtl_pon_pdc_configure(&pon)==-EBUSY && !public_inits);
   mutex_lock(&pon.control_lock);assert(rtl_pon_gpon_activation_locked(&pon,true)==-EBUSY);mutex_unlock(&pon.control_lock);
   if(test==5){
    mutex_lock(&pon.control_lock);rtl_pon_gpon_stop_locked(&pon);mutex_unlock(&pon.control_lock);
    assert(!pon.gpon_recover && !pon.gpon_drain.phase && !pon.mac_prepared && !pon.puc_configured && !pon.pdc_configured);
    assert(!gpon_control && !(control&(BIT(8)|BIT(9))) && gpio==1 && !top[1]);
    assert(allocations==frees);continue;
   }
   pon.fe_enabled=false;probe_busy=false;busy_flush=false;retain_packets=false;
   if(test>=3){regs[0x80dc/4]&=~BIT(31);bytes[11]=packets[11]=0;}
   unsigned int previous_flushes=flushes;
   rtl_pon_gpon_recovery(&pon.gpon_recovery_work);
   assert(!pon.fe_enabled && flushes==(test==2?1:previous_flushes));
  }
  assert(!pon.gpon_recover && !pon.gpon_drain.phase && !pon.gpon_activation_error && top[1]==1);
  assert(resets==1 && gtc_flushes==32 && flushes==(test?1U:0U) && probes==(test==2?1U:0U));
  assert(!memcmp(pvt,saved_pvt,sizeof(pvt)) && alloc_table[17]==((test==1||test==7)?1U:3U) && alloc_table[1063]==7);
  if(test==1||test==7){
   assert(pon.gpon_omcc_ready && pon.gpon_omcc_onu_id==18 && pon.gpon_omcc_gem==3333 && alloc_table[18]==3 && omci.carrier);
   for(unsigned int i=0;i<8;i++)assert(gem_table[i]==3333);
  }
  assert(regs[0x808c/4]==0x20030002 && regs[0x80e0/4]==0x12000006 && regs[0x61c4/4]==0x05020000 && regs[0x6114/4]==0x460062);
  assert(regs[0x81bc/4]==65535);for(unsigned int i=1;i<8;i++)assert(!regs[(0x81bc-i*4)/4]);
  for(unsigned int i=0;i<16;i++)assert(regs[(0x8090+i*4)/4]==0x55555555);
  assert(allocations==frees && !pon.lock && !pon.control_lock);
  assert(!rtl_pon_puc_configure(&pon) && !rtl_pon_pdc_configure(&pon) && public_inits==2);
  pon.mode=RTL_PON_XGPON;pon.xgpon_recovery_phase=3;
  assert(rtl_pon_puc_configure(&pon)==-EBUSY && rtl_pon_pdc_configure(&pon)==-EBUSY && public_inits==2);
 }
 puts("Production GPON drain: queue/probe/NI failure ownership; current O5 identity restored without another IRQ; OMCC timeout retries bindings without a second drain; ASan/UBSan passed");
}
'''
names = ('rtl_gpon_read', 'rtl_gpon_write', 'rtl_gpon_indirect', 'rtl_gpon_drain_map',
         'rtl_gpon_drain_alloc', 'rtl_gpon_drain_count', 'rtl_gpon_drain_probe', 'rtl_gpon_drain_run',
         'rtl_gpon_omcc_assign', 'rtl_gpon_omcc_map', 'rtl_gpon_omcc_restore',
         'rtl_pon_gpon_stop_locked', 'rtl_pon_gpon_activation_locked', 'rtl_pon_gpon_recovery')
other = (drivers / 'rtl9617c-pon.c').read_text()
with tempfile.TemporaryDirectory(prefix='gpon-drain-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    code = model + ''.join(function(source, name) for name in names)
    code += function(other, 'rtl_pon_puc_configure') + function(other, 'rtl_pon_pdc_configure') + cases
    (work / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(drivers), str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
