#!/usr/bin/env python3
"""Run actual forced-teardown BID walks across partial EQ admission."""
from pathlib import Path
import subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h'
s=p.read_text();a=s.index('static int ni_test_remove(');a=s.index('\tfor (i = 0; i < 128; i++) {',a);b=s.index('\tni_restore_registers(tx);',a)
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
struct rtl_ax_tx {void *dma_device;struct {void *memory;} pe;struct {void *regs;unsigned cpu,eq;} ni;};
static unsigned char payloads[5*128];
static unsigned returned[5],count,limit;
static u32 get_unaligned_le32(const void *p) {u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p) {memcpy(p,&v,4);}
static void *phys_to_virt(u32 a) {assert(a>=0x6000000 && a<0x6000000+sizeof(payloads));return payloads+(a-0x6000000);}
static void dma_sync_single_for_cpu(void *dev,u32 a,unsigned bytes,unsigned direction) {(void)dev;assert(bytes==4 || bytes==16);assert(direction==1);(void)phys_to_virt(a);}
#define readl_poll_timeout(a,v,c,d,t) ({(void)(a);(void)(d);(void)(t);v=count<limit?BIT(31):0;(c)?0:-ETIMEDOUT;})
static void writel(u32 v,void *p) {(void)p;assert(count<limit && count<5);assert((v&127)==6);unsigned a=v&~127U;for(unsigned i=0;i<count;i++)assert(returned[i]!=a);returned[count++]=a;memset(phys_to_virt(a),0xa5,128);}
static int cleanup(struct rtl_ax_tx *tx) {int ret;u32 status;unsigned i;
'''
test=r'''
return 0;
}
int main(void) {
 for(unsigned stop=0;stop<5;stop+=(stop?3:1)) {
  unsigned char memory[0x20000]={0},registers[0x10000]={0};
  struct rtl_ax_tx tx={.pe={memory},.ni={.regs=registers,.cpu=3,.eq=6}};
  memset(payloads,0,sizeof(payloads));count=0;limit=stop;
  put_unaligned_le32(0x6000000,memory+0xc400);
  put_unaligned_le32(0x6000080,phys_to_virt(0x6000000));
  put_unaligned_le32(0x6000100,phys_to_virt(0x6000080));
  put_unaligned_le32(0x6000180,memory+0x15000);
  put_unaligned_le32(0x6000200,phys_to_virt(0x6000180));
  assert(cleanup(&tx)==-ETIMEDOUT && count==stop);
  unsigned owned=get_unaligned_le32(memory+0xc400),queued=get_unaligned_le32(memory+0x15000);
  assert(owned==(stop<3?0x6000000+stop*128:0));
  assert(queued==(stop<3?0x6000180:0x6000000+stop*128));
  limit=5;assert(!cleanup(&tx) && count==5);
  for(unsigned i=0;i<5;i++)assert(returned[i]==0x6000000+i*128);
  assert(!get_unaligned_le32(memory+0xc400) && !get_unaligned_le32(memory+0x15000));
  assert(!cleanup(&tx) && count==5);
 }
 puts("Forced NI teardown: complete aggregate and queued BID chains; EQ timeout retains the remaining head; retry never reads or returns reused BIDs");
}
'''
with tempfile.TemporaryDirectory(prefix='ni-bid-teardown-',dir=ROOT/'artifacts') as tmp:
 tmp=Path(tmp);c=tmp/'test.c';c.write_text(common+s[a:b]+test)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(c),'-o',str(tmp/'test')],check=True)
 subprocess.run([str(tmp/'test')],check=True)
