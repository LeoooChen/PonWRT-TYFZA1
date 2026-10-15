#!/usr/bin/env python3
"""Execute the production XGTC IRQ for short O5/O6 signal-loss transitions."""
from pathlib import Path
import argparse,re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
relative=Path('port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek')
drivers=root/relative
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--revision');args=parser.parse_args()
source=subprocess.check_output(['git','show',f'{args.revision}:{relative}/rtl9617c-pon-xgpon.c'],cwd=root,text=True) if args.revision else (drivers/'rtl9617c-pon-xgpon.c').read_text()
def function(name):
 start=re.search(r'^(?:static )?(?:int|void|unsigned int) '+name+r'\(',source,re.M).start()
 end=source.index('{',start)+1;depth=1
 while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]+'\n'
model=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-xgpon-hw.h"
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h)))&(~0U<<(l)))
#define HZ 100
#define lockdep_assert_held(p) assert(*(p))
#define spin_lock_bh(p) ((void)(p))
#define spin_unlock_bh(p) ((void)(p))
#define dev_info(...) ((void)0)
#define readl_poll_timeout(p,v,c,d,t) ({v=readl(p);(c)?0:-1;})
struct net_device {bool carrier;};
struct work {unsigned int pending;};
struct rtl9617c_pon {
 int lock,mode,omci_key_lock,xgpon_activation_error;
 unsigned char *mac;void *serdes_init,*dev;
 bool xgpon_enabled,xgpon_omcc_ready,xgpon_key_ready,omci_master_key_valid,itu_paths_ready,xgpon_recover,fe_enabled;
 u32 xgpon_last_irq,xgpon_last_transitions;u64 xgpon_irq_count,xgpon_deactivations,ploam_mic_errors,ploam_rx,itu_line_epoch;
 unsigned int xgpon_key_valid,xgpon_key_state,xgpon_last_state,xgpon_omcc_id;
 u8 xgpon_serial[8];struct net_device *omci_device;struct work xgpon_recovery_work;
};
static unsigned int invalidated;
static bool expect_key_reset;
static unsigned int *mmio;
static unsigned int readl(const void *p){unsigned int v=*(const unsigned int*)p;return p==mmio+0x4618/4?v&~BIT(31):v;}
static void writel(unsigned int v,void *p){*(unsigned int*)p=v;}
static void rtl_pon_update(void *p,unsigned int mask,unsigned int v){writel((readl(p)&~mask)|v,p);}
static unsigned int get_unaligned_be16(const void *p){const u8 *b=p;return b[0]<<8|b[1];}
static int rtl_pon_xgpon_omci_key(struct rtl9617c_pon *p,const u8 *tag){
 if(expect_key_reset){assert(!p->xgpon_key_ready && !p->omci_master_key_valid && !p->xgpon_omcc_ready && tag[0]==0x42);expect_key_reset=false;}
 p->xgpon_key_ready=true;return 0;
}
static int rtl_pon_xgpon_ack_update(struct rtl9617c_pon *p){(void)p;return 0;}
static void rtl_pon_xgpon_key_irq(struct rtl9617c_pon *p,u32 a,u32 b){(void)p;(void)a;(void)b;}
static void netif_carrier_on(struct net_device *d){d->carrier=true;}
static void netif_carrier_off(struct net_device *d){d->carrier=false;}
static bool netif_carrier_ok(struct net_device *d){return d->carrier;}
static int rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *p,enum rtl_pon_mode mode){assert(p->lock && p->mode==mode);p->itu_paths_ready=false;invalidated++;return 0;}
static void rtl_pon_itu_carrier(struct rtl9617c_pon *p){(void)p;}
static void schedule_delayed_work(struct work *w,unsigned int delay){assert(delay==HZ);w->pending++;}
'''
cases=r'''
int main(void){
 setvbuf(stdout,NULL,_IOLBF,0);
 for(int mode=RTL_PON_XGPON;mode<=RTL_PON_XGSPON;mode++)
 for(unsigned int state=3;state<=4;state++){
  u32 regs[0xc000/4]={},sds=1U<<4;struct net_device omci={.carrier=true};
  mmio=regs;
  struct rtl9617c_pon pon={.lock=1,.mode=mode,.mac=(void*)regs,.serdes_init=&sds,
   .omci_device=&omci,.xgpon_enabled=true,.xgpon_omcc_ready=true,.xgpon_key_ready=true,
   .omci_master_key_valid=true,.itu_paths_ready=true,.fe_enabled=true,.xgpon_last_state=3,
   .xgpon_key_valid=3,.xgpon_key_state=4,.xgpon_omcc_id=17};
  regs[0x4004/4]=mode==RTL_PON_XGSPON?2:1;
  regs[0x4d28/4]=regs[0x4d2c/4]=BIT(6)|(state==3?BIT(27):0);
  regs[0x4d30/4]=state;invalidated=0;
  rtl_pon_xgpon_irq(&pon);
  printf("mode=%d ACTP=%u recovery=%u scheduled=%u active=%u SDS=%x\n",mode,state,pon.xgpon_recover,pon.xgpon_recovery_work.pending,regs[0x4004/4],sds);
  assert(!pon.xgpon_recover && !pon.xgpon_recovery_work.pending);
  assert(regs[0x4004/4]==(mode==RTL_PON_XGSPON?2U:1U) && sds==BIT(4));
  assert(pon.xgpon_key_ready && pon.xgpon_omcc_ready && pon.omci_master_key_valid);
  assert(pon.fe_enabled && !pon.itu_line_epoch);
  assert(omci.carrier==(state==3) && invalidated==(state==4));
  if(state==4){
   regs[0x4d28/4]=regs[0x4d2c/4]=BIT(27);regs[0x4d30/4]=3;
   rtl_pon_xgpon_irq(&pon);
   assert(omci.carrier && !pon.xgpon_recover && !pon.itu_line_epoch);
  }
 }
 for(int mode=RTL_PON_XGPON;mode<=RTL_PON_XGSPON;mode++){
  u32 regs[0xc000/4]={},sds=BIT(4);struct net_device omci={};u8 packet[48]={};
  struct rtl9617c_pon pon={.lock=1,.mode=mode,.mac=(void*)regs,.serdes_init=&sds,
   .omci_device=&omci,.xgpon_enabled=true,.xgpon_key_ready=true,.fe_enabled=true,
   .xgpon_last_state=1,.xgpon_serial={'T','E','S','T',1,2,3,4}};
  mmio=regs;packet[2]=3;packet[5]=17;memcpy(packet+6,pon.xgpon_serial,8);
  for(unsigned int i=0;i<48;i+=4)regs[(0x4b80+i)/4]=(u32)packet[i]<<24|(u32)packet[i+1]<<16|(u32)packet[i+2]<<8|packet[i+3];
  regs[0x4b28/4]=BIT(31);regs[0x4d30/4]=2;
  regs[0x4d28/4]=regs[0x4d2c/4]=BIT(12);
  rtl_pon_xgpon_irq(&pon);
  printf("mode=%d Assign-ONU-ID in O4: OMCC=%u carrier=%u\n",mode,pon.xgpon_omcc_ready,omci.carrier);
  assert(pon.xgpon_omcc_ready && pon.xgpon_omcc_id==17 && !omci.carrier);
  regs[0x4d30/4]=3;regs[0x4d28/4]=regs[0x4d2c/4]=BIT(17);
  rtl_pon_xgpon_irq(&pon);
  assert(omci.carrier && pon.xgpon_omcc_ready && !pon.itu_line_epoch);
 }
 for(int mode=RTL_PON_XGPON;mode<=RTL_PON_XGSPON;mode++)
 for(unsigned int state=2;state<=3;state++){
  u32 regs[0xc000/4]={},sds=BIT(4);struct net_device omci={.carrier=true};u8 frames[2][48]={};
  struct rtl9617c_pon pon={.lock=1,.mode=mode,.mac=(void*)regs,.serdes_init=&sds,
   .omci_device=&omci,.xgpon_enabled=true,.xgpon_key_ready=true,.xgpon_omcc_ready=true,
   .omci_master_key_valid=true,.itu_paths_ready=true,.fe_enabled=true,.xgpon_last_state=3,
   .xgpon_key_valid=3,.xgpon_key_state=4,.xgpon_omcc_id=77,.xgpon_serial={'T','E','S','T',1,2,3,4}};
  mmio=regs;frames[0][2]=1;frames[0][25]=0x42;
  frames[1][2]=3;frames[1][5]=17;memcpy(frames[1]+6,pon.xgpon_serial,8);
  for(unsigned int b=0;b<2;b++)for(unsigned int i=0;i<48;i+=4)
   regs[(0x4b80+b*48+i)/4]=(u32)frames[b][i]<<24|(u32)frames[b][i+1]<<16|(u32)frames[b][i+2]<<8|frames[b][i+3];
  regs[0x4b28/4]=BIT(31)|BIT(30);regs[0x4b2c/4]=BIT(31);
  regs[0x4d30/4]=state;regs[0x4d28/4]=regs[0x4d2c/4]=BIT(11)|BIT(12);
  expect_key_reset=true;invalidated=0;rtl_pon_xgpon_irq(&pon);
  assert(!expect_key_reset && pon.itu_line_epoch==1 && invalidated==1 && !pon.itu_paths_ready);
  assert(pon.xgpon_key_ready && pon.xgpon_omcc_ready && pon.xgpon_omcc_id==17);
  assert(!pon.omci_master_key_valid && !pon.xgpon_key_valid && !pon.xgpon_key_state);
  assert(omci.carrier==(state==3) && !pon.xgpon_recover);
 }
 puts("Actual XGTC IRQ: O6 grace, Assign-ONU-ID O4->O5, and coalesced deactivation/new burst profile/identity preserve the correct session configuration");
}
'''
with tempfile.TemporaryDirectory(prefix='xgtc-o6-',dir=root/'artifacts') as temp:
 work=Path(temp);c=work/'test.c';binary=work/'test'
 c.write_text(model+''.join(function(n) for n in ('rtl_xgpon_read','rtl_xgpon_write','rtl_xgpon_indirect','rtl_pon_xgpon_irq'))+cases)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-sign-compare','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(drivers),str(c),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
