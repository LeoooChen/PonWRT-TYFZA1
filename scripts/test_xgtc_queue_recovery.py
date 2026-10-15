#!/usr/bin/env python3
"""Execute native queue recovery with saved tables and a failed restore retry."""
from pathlib import Path
import re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
drivers=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
def function(source,name):
 start=re.search(r'^(?:static )?(?:int|void|unsigned int|u32) '+name+r'\(',source,re.M).start()
 end=source.index('{',start)+1;depth=1
 while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]+'\n'
model=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-xgtc-recovery.h"
#include "rtl9617c-xgpon-hw.h"
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h)))&(~0U<<(l)))
#define HZ 100
#define lockdep_assert_held(p) assert(*(p))
#define readl_poll_timeout(p,v,c,d,t) ({v=readl(p);(c)?0:-ETIMEDOUT;})
#define to_delayed_work(w) (w)
#define container_of(p,t,m) ((t*)((char*)(p)-offsetof(t,m)))
struct work_struct {unsigned int pending;};
struct device {void *data;};
struct rtl_xram {int tx_lock;void *ni;};
struct rtl9617c_pon {int lock,control_lock;enum rtl_pon_mode mode;bool xgpon_recover,fe_enabled;unsigned char xgpon_recovery_phase;int xgpon_activation_error;unsigned char *mac;void *serdes_init,*control;struct device *ni_dev;struct rtl_xgtc_puc_saved xgpon_puc_saved;struct work_struct xgpon_recovery_work;bool xgpon_enabled,xgpon_omcc_ready,omci_master_key_valid,xgpon_key_ready;unsigned long long itu_line_epoch;unsigned int xgpon_key_valid,xgpon_key_state;int omci_key_lock,mpcp_irq;void *tx_disable,*top_irq,*omci_device;};
#define netdev_priv(p) (p)
#define dev_get_drvdata(d) ((d)->data)
#define spin_lock_irqsave(p,f) do{assert(!*(p));*(p)=1;(f)=0;}while(0)
#define spin_unlock_irqrestore(p,f) do{assert(*(p));*(p)=0;(void)(f);}while(0)
static u32 regs[0xc000/4],ni[0x10000/4],control,sds;
static u32 tables[5][256][5];
static const unsigned int access_reg[5]={0x8000,0x8060,0x81c4,0x81ec,0x80e8};
static const unsigned int data_reg[5]={0x8014,0x8074,0x81cc,0x81f4,0x80ec};
static const unsigned int count[5]={5,5,2,2,1};
static unsigned int flushes,initializations,accesses,fail_access;
#define spin_lock_bh(p) ((void)(p))
#define spin_unlock_bh(p) ((void)(p))
static void cancel_delayed_work(struct work_struct *w){w->pending=0;}
static void synchronize_irq(int irq){(void)irq;}
static int rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *p,enum rtl_pon_mode mode){assert(p->lock && p->mode==mode);return 0;}
static void netif_carrier_off(void *dev){(void)dev;}
static void gpiod_set_value_cansleep(void *gpio,int value){*(int*)gpio=value;}
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void udelay(unsigned int n){assert(n==500);}
static void msleep(unsigned int n){assert(n==5);}
static u32 readl(const void *p){return *(const u32*)p;}
static void writel(u32 value,void *p){
 *(u32*)p=value;
 uintptr_t at=(uintptr_t)p-(uintptr_t)regs;
 if(at==0x4004 && (value&BIT(31))){flushes++;*(u32*)p=value&~BIT(31);}
 for(unsigned int t=0;t<5;t++)if(at==access_reg[t] && (value&BIT(31))){
  assert(!(ni[0x610c/4]&65535) && !(regs[0x4004/4]&3));
  if(++accesses==fail_access)return;
  unsigned int i=value&255;
  for(unsigned int w=0;w<count[t];w++){
   if(value&BIT(30))tables[t][i][w]=regs[(data_reg[t]-w*4)/4];
   else regs[(data_reg[t]-w*4)/4]=tables[t][i][w];
  }
  *(u32*)p=value&~BIT(31);
 }
}
static void rtl_pon_update(void *p,u32 mask,u32 value){writel((readl(p)&~mask)|value,p);}
static void schedule_delayed_work(struct work_struct *w,unsigned int delay){assert(delay==HZ);w->pending++;}
static int rtl_pon_puc_configure_locked(struct rtl9617c_pon *p){
 assert(p->control_lock && !p->lock && !(ni[0x610c/4]&65535));
 initializations++;
 for(unsigned int i=0;i<5;i++)regs[rtl_xgtc_control_regs[i]/4]=0x44000000;
 for(unsigned int i=0;i<5;i++)for(unsigned int j=0;j<256;j++)for(unsigned int k=0;k<5;k++)tables[i][j][k]=0x8000e000;
 return 0;
}
'''
cases=r'''
int main(void){
 setvbuf(stdout,NULL,_IOLBF,0);
 for(unsigned int mode=RTL_PON_XGPON;mode<=RTL_PON_XGSPON;mode++){
  memset(regs,0,sizeof(regs));memset(tables,0,sizeof(tables));memset(ni,0,sizeof(ni));
  control=BIT(8)|0x30e;sds=BIT(4)|3;ni[0x610c/4]=0xa5c36db5;
  struct rtl_xram engine={.ni=ni};struct device dev={&engine};
  struct rtl9617c_pon pon={.mode=mode,.mac=(void*)regs,.control=&control,.serdes_init=&sds,.ni_dev=&dev,.xgpon_recover=true,.fe_enabled=true};
  regs[0x4004/4]=mode==RTL_PON_XGPON?0x41:0x82;regs[0x4600/4]=BIT(2)|0x7800;
  regs[0x4d0c/4]=0x12345;regs[0x4d10/4]=0x6789a;regs[0x4d30/4]=1;
  for(unsigned int i=0;i<5;i++)regs[rtl_xgtc_control_regs[i]/4]=0xa59c1234+i;
  for(unsigned int i=0;i<16;i++)regs[(0x8090+i*4)/4]=0x12345678+i;
  for(unsigned int i=0;i<3;i++)regs[(0x80d0+i*4)/4]=0xabc+13*i;
  for(unsigned int i=0;i<8;i++)regs[(0x81bc-i*4)/4]=0xfeed0000+i;
  for(unsigned int i=0;i<32;i++){
   tables[0][i][0]=0xc1a0b048;tables[0][i][1]=0x1c0a0300;
   tables[0][i][2]=0x05031083;tables[0][i][3]=9;tables[0][i][4]=0;
  }
  for(unsigned int i=0;i<256;i++){
   tables[2][i][0]=0x12340000+i;tables[2][i][1]=0x02340000+i;
   tables[3][i][0]=0x77770000+i;tables[3][i][1]=0x0fff0000+i;
   tables[4][i][0]=0xabcdef00|i;
  }
  flushes=initializations=accesses=0;
  /* Fail the first indirect command of restore, after the original snapshot
   * and PUC initialization. The subsequent attempt must use that snapshot. */
  fail_access=32+256*3+1;
  rtl_pon_xgpon_recovery(&pon.xgpon_recovery_work);
  assert(pon.xgpon_recover && pon.xgpon_activation_error==-ETIMEDOUT && pon.xgpon_recovery_phase==3);
  assert(initializations==1 && flushes==32 && (ni[0x610c/4]&65535)==0 && !(regs[0x4004/4]&3));
  assert(pon.fe_enabled);
  struct rtl_xgtc_puc_saved snapshot=pon.xgpon_puc_saved;
  pon.fe_enabled=false;fail_access=0;regs[0x8000/4]&=~BIT(31);
  rtl_pon_xgpon_recovery(&pon.xgpon_recovery_work);
  assert(!pon.xgpon_recover && !pon.xgpon_activation_error && !pon.xgpon_recovery_phase);
  assert(!pon.fe_enabled && initializations==1 && flushes==32);
  assert(!memcmp(&snapshot,&pon.xgpon_puc_saved,sizeof(snapshot)));
  assert(ni[0x610c/4]==0xa5c36db5 && sds==(BIT(4)|3));
  assert(regs[0x4004/4]==snapshot.mac && regs[0x4d0c/4]==snapshot.to1 && regs[0x4d10/4]==snapshot.to2);
  for(unsigned int i=0;i<5;i++)assert((regs[rtl_xgtc_control_regs[i]/4]&rtl_xgtc_control_masks[i])==(snapshot.control[i]&rtl_xgtc_control_masks[i]));
  for(unsigned int i=0;i<32;i++)for(unsigned int j=0;j<5;j++){
   unsigned int masks[5]={~0U,~0U,0xffff10ff,~0U,65535};
   assert((tables[0][i][j]&masks[j])==(snapshot.pvt[i][j]&masks[j]));
  }
  for(unsigned int i=0;i<256;i++){
   assert(tables[2][i][0]==snapshot.cir[i][0] && (tables[2][i][1]&0x7fffffff)==snapshot.cir[i][1]);
   assert(tables[3][i][0]==snapshot.pir[i][0] && (tables[3][i][1]&0x7fffffff)==snapshot.pir[i][1]);
   assert((tables[4][i][0]&255)==(snapshot.backpressure[i]&255));
   assert((tables[4][i][0]&~255U)==0x8000e000);
  }
  for(unsigned int i=0;i<8;i++)assert(regs[(0x81bc-i*4)/4]==snapshot.valid[i]);
  const struct rtl_dq_io io={&pon,rtl_xgpon_read,rtl_xgpon_write,rtl_xgpon_indirect};
  for(unsigned int state=0;state<7;state++)for(unsigned int sub=0;sub<2;sub++){
   u32 value=0xa5030000|state|sub<<4;regs[0x4d30/4]=value;
   rtl_xgtc_restart_sync(&io);
   assert(regs[0x4d30/4]==((state==0||state==6)&&sub?(value&~(3U<<16))|BIT(16):value));
  }
  regs[0x4d30/4]=3;pon.xgpon_recover=true;
  unsigned int prior_accesses=accesses;
  rtl_pon_xgpon_recovery(&pon.xgpon_recovery_work);
  assert(!pon.xgpon_recover && accesses==prior_accesses && ni[0x610c/4]==0xa5c36db5);
  regs[0x4d30/4]=1;pon.xgpon_recover=true;fail_access=accesses+1;
  rtl_pon_xgpon_recovery(&pon.xgpon_recovery_work);
  assert(pon.xgpon_recovery_phase==1 && pon.xgpon_activation_error==-ETIMEDOUT);
  assert(pon.xgpon_puc_saved.mac==snapshot.mac && !(ni[0x610c/4]&65535));
  int gpio=0;u32 top[2]={0,1};pon.tx_disable=&gpio;pon.top_irq=top;
  mutex_lock(&pon.control_lock);rtl_pon_xgpon_stop_locked(&pon);mutex_unlock(&pon.control_lock);
  assert(!pon.xgpon_recovery_phase && !pon.xgpon_recover && gpio==1 && ni[0x610c/4]==0xa5c36db5);
  assert(!pon.lock && !pon.control_lock && !engine.tx_lock);
 }
 puts("Actual XGTC worker/NI gate/PUC helpers:32 T-CONT flushes, configuration-only restore, retained snapshot across timeout, preserved admission mask and explicit FE disable; no SerDes or FE rebuild callback exists");
}
'''
src=(drivers/'rtl9617c-pon-xgpon.c').read_text()
text=model+function((drivers/'rtl9617c-xram.c').read_text(),'rtl9617c_ni_pon_gate')+''.join(function(src,n) for n in ('rtl_xgpon_read','rtl_xgpon_write','rtl_xgpon_indirect','rtl_pon_xgpon_stop_locked','rtl_pon_xgpon_recovery'))+cases
with tempfile.TemporaryDirectory(prefix='xgtc-queue-recovery-',dir=root/'artifacts') as temp:
 work=Path(temp);c=work/'test.c';binary=work/'test';c.write_text(text)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(drivers),str(c),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
