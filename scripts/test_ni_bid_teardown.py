#!/usr/bin/env python3
"""Run actual forced-teardown BID walks across partial EQ admission."""
from pathlib import Path
import argparse
import subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h'
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--revision');args=parser.parse_args()
s=subprocess.check_output(['git','show',args.revision+':'+str(p.relative_to(ROOT))],cwd=ROOT,text=True) if args.revision else p.read_text();a=s.index('static int ni_test_remove(');a=s.index('\tfor (i = 0; i < RTL_AX_NI_OWNER_SLOTS; i++) {',a);b=s.index('\tret = ni_restore_registers(tx);',a)
common=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define DMA_FROM_DEVICE 1
#define WRITE_ONCE(v,x) ((v)=(x))
#define cpu_to_le32(v) (v)
#define le32_to_cpu(v) (v)
#include "linux/rtl9617c-mcast-pe.h"
#include "tx-ownership.h"
struct rtl_ax_tx {void *dma_device;struct {void *memory;} pe;struct {void *regs;unsigned cpu,eq;} ni;};
static unsigned char payloads[5*128];
static unsigned returned[5],count,limit,credit_limit,credit_valid;
static void *registers_base;
static u32 get_unaligned_le32(const void *p) {u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p) {memcpy(p,&v,4);}
static void *phys_to_virt(u32 a) {assert(a>=0x6000000 && a<0x6000000+sizeof(payloads));return payloads+(a-0x6000000);}
static void dma_sync_single_for_cpu(void *dev,u32 a,unsigned bytes,unsigned direction) {(void)dev;assert(bytes==28);assert(direction==1);(void)phys_to_virt(a);}
#define readl_poll_timeout(a,v,c,d,t) ({(void)(d);(void)(t);v=readl(a);(c)?0:-ETIMEDOUT;})
static void writel(u32 v,void *p) {(void)p;assert(count<limit && count<credit_limit && credit_valid && count<5);assert((v&127)==6);unsigned a=v&~127U;for(unsigned i=0;i<count;i++)assert(returned[i]!=a);returned[count++]=a;memset(phys_to_virt(a),0xa5,128);}
static u32 readl(void *p){if(p==registers_base+0x6328+6*4)return (credit_valid?BIT(31):0)|(credit_limit-count);return count<limit?BIT(31):0;}
static int rtl9617c_wifi_retire(unsigned pool){assert(pool==0);return 0;}
static int cleanup(struct rtl_ax_tx *tx) {int ret;u32 status;unsigned i;
'''
test=r'''
return 0;
}
int main(void) {
 for(unsigned owner=0;owner<=RTL_AX_NI_DISCARD;owner+=1024)
 for(unsigned gate=0;gate<3;gate++)for(unsigned stop=0;stop<5;stop+=(stop?3:1)) {
  unsigned char memory[0x80000]={0},registers[0x10000]={0};
  struct rtl_ax_tx tx={.pe={memory},.ni={.regs=registers,.cpu=3,.eq=6}};
  memset(payloads,0,sizeof(payloads));count=0;registers_base=registers;
  limit=gate?5:stop;credit_limit=gate==1?stop:5;credit_valid=1;
  if(gate==2){credit_valid=0;credit_limit=5;}
  unsigned expected=gate==2?0:stop;
  struct rtl_fttr_pe_drain *drain=(void *)(memory+RTL_FTTR_PE_DRAIN);drain->pending=3;
  struct rtl_fttr_pe_drain *mc=(void *)(memory+RTL_MC_PE_DRAIN);mc->pending=3;
  for(unsigned i=1;i<4;i++)put_unaligned_le32(265,phys_to_virt(0x6000000+i*128)+16);
  for(unsigned i=0;i<5;i+=2)put_unaligned_le32(2,phys_to_virt(0x6000000+i*128)+20);
  put_unaligned_le32(0x6000000,memory+RTL_AX_NI_OWNERS+owner*8);
  put_unaligned_le32(0x6000080,phys_to_virt(0x6000000));
  put_unaligned_le32(0x6000100,phys_to_virt(0x6000080));
  put_unaligned_le32(0x6000180,memory+0x15000);
  put_unaligned_le32(0x6000200,phys_to_virt(0x6000180));
  assert(cleanup(&tx)==-ETIMEDOUT && count==expected);assert(drain->pending==3-(expected+1)/2);assert(mc->pending==3-(expected>1?(expected>4?3:expected-1):0));
  unsigned owned=get_unaligned_le32(memory+RTL_AX_NI_OWNERS+owner*8),queued=get_unaligned_le32(memory+0x15000);
  assert(owned==(expected<3?0x6000000+expected*128:0));
  assert(queued==(expected<3?0x6000180:0x6000000+expected*128));
  limit=credit_limit=5;credit_valid=1;assert(!cleanup(&tx) && count==5&&!drain->pending&&!mc->pending);
  for(unsigned i=0;i<5;i++)assert(returned[i]==0x6000000+i*128);
  assert(!get_unaligned_le32(memory+RTL_AX_NI_OWNERS+owner*8) && !get_unaligned_le32(memory+0x15000));
  assert(!cleanup(&tx) && count==5);
 }
 puts("Forced NI teardown: complete aggregate and queued BID chains; EQ timeout retains the remaining head; retry never reads or returns reused BIDs");
}
'''
with tempfile.TemporaryDirectory(prefix='ni-bid-teardown-',dir=ROOT/'artifacts') as tmp:
 tmp=Path(tmp);c=tmp/'test.c';c.write_text(common+s[a:b]+test)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(p.parent),'-I'+str(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(c),'-o',str(tmp/'test')],check=True)
 subprocess.run([str(tmp/'test')],check=True)
