#!/usr/bin/env python3
"""Execute NI start/stop register programming and the PE/CCI stale-header model."""
from pathlib import Path
import subprocess,tempfile,argparse
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path);parser.add_argument('--revision');args=parser.parse_args()
source=(args.source or root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h').read_text()
if args.revision:source=subprocess.check_output(['git','show',args.revision+':port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h'],cwd=root,text=True)
a=source.index('static int ni_uplink_stop(');b=source.index('\nstatic bool ni_es_empty' if 'static bool ni_es_empty' in source else '\nstatic bool ni_epps_empty',a)
code=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
typedef uint32_t u32;typedef unsigned char u8;
#define BIT(n) (1U<<(n))
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_NI_TX_MASK 1023
#define RTL_AX_NI_TX_DEPTH 10
#define RTL_AX_NI_TX_BYTES 8192
#define GFP_KERNEL 0
#define lower_32_bits(n) ((u32)(n))
struct rtl_ax_tx {void *dma_device,*rx,*hw;struct {bool uplink_ready;void *uplink_memory,*vp,*dma;u32 uplink_dma,vp_saved[5],vp_index;}ni;};
static u8 memory[0x20000],regs[0x1000];static bool paused;static unsigned allocations,releases;static bool load_fail;static unsigned base_writes,enable_fault;static int pause_error;
static void *rtl_ax_rx_pe_memory(void *p){assert(p==memory);return p;}
static u32 get_unaligned_le32(const void*p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void*p){memcpy(p,&v,4);}
static u32 readl(void*p){u32 v=get_unaligned_le32(p);if((u8*)p==regs+0x104&&!load_fail)v&=~(BIT(31)|BIT(30));return v;}
static void writel(u32 v,void*p){if((u8*)p==regs+0x100 && ((enable_fault==1 && !v)||(enable_fault==2 && v)))return;if((u8*)p==regs+0x108||(u8*)p==regs+0x10c)base_writes++;put_unaligned_le32(v,p);}
#define readl_poll_timeout(a,v,c,d,t) ({v=readl(a);(c)?0:-ETIMEDOUT;})
static void dma_wmb(void){}
static int rtl_ax_tx_pause(struct rtl_ax_tx*t){(void)t;if(pause_error)return pause_error;paused=true;return 0;}
static void rtl_ax_tx_open(struct rtl_ax_tx*t){(void)t;paused=false;}
static void *dma_alloc_coherent(void*d,unsigned n,u32*a,int f){(void)d;(void)f;assert(paused&&n==8192);*a=0x5000000;allocations++;return calloc(1,n);}
static void dma_free_coherent(void*d,unsigned n,void*p,u32 a){(void)d;(void)n;(void)a;assert(paused);releases++;free(p);}
static void rtl_ax_rx_uplink_start(void*r,void*h){(void)r;(void)h;assert(paused);}
'''
code+=source[a:b]
code+=r'''
int main(void){

 unsigned cases=0;
 for(unsigned vp=9;vp<=10;vp++)for(unsigned failure=0;failure<6;failure++){
  memset(memory,0,sizeof(memory));memset(regs,0,sizeof(regs));load_fail=false;enable_fault=pause_error=0;
  struct rtl_ax_tx t={.rx=memory,.ni={.vp=regs+0x100,.dma=regs,.vp_index=vp}};
  writel(7,regs+0x108);writel(0x1234,regs+0x10c);writel(0xa5555555,regs+0x14);
  unsigned before_alloc=allocations,before_free=releases;
  if(failure==0){
   load_fail=true;
   assert(ni_uplink_start(&t)==-ETIMEDOUT && t.ni.uplink_memory && !t.ni.uplink_ready);
  }else{
   assert(!ni_uplink_start(&t) && t.ni.uplink_ready);
   unsigned old=base_writes;
   assert(!ni_uplink_start(&t) && base_writes==old && allocations==before_alloc+1);
   if(failure==1)pause_error=-ETIMEDOUT;
   if(failure==2)writel(1,regs+0x118);
   if(failure==3)enable_fault=1;
   if(failure==4){load_fail=true;put_unaligned_le32(BIT(31),regs+0x104);}
   if(failure==5){enable_fault=2;assert(!ni_uplink_stop(&t));
    assert(ni_uplink_start(&t)==-EIO && !t.ni.uplink_ready && !t.ni.uplink_memory);
    enable_fault=0;assert(!ni_uplink_start(&t) && t.ni.uplink_ready);assert(!ni_uplink_stop(&t));cases++;continue;}
   assert(ni_uplink_stop(&t)==(failure==3?-EIO:-ETIMEDOUT));
  }
  assert(t.ni.uplink_memory && !t.ni.uplink_ready && releases==before_free);
  void *owned=t.ni.uplink_memory;u32 snapshot[5];memcpy(snapshot,t.ni.vp_saved,sizeof(snapshot));
  unsigned writes=base_writes;
  /* Failed stop/load is never mistaken for a successfully existing queue. */
  assert(ni_uplink_start(&t)==(failure==3?-EIO:-ETIMEDOUT));
  assert(t.ni.uplink_memory==owned && !t.ni.uplink_ready && releases==before_free && allocations==before_alloc+1);
  assert(!memcmp(snapshot,t.ni.vp_saved,sizeof(snapshot)) && base_writes==writes);
  assert(!get_unaligned_le32(memory+PE_PROBE_MAILBOX+372));
  load_fail=false;enable_fault=pause_error=0;put_unaligned_le32(0,regs+0x104);writel(0,regs+0x118);
  assert(!ni_uplink_start(&t) && t.ni.uplink_ready && allocations==before_alloc+2 && releases==before_free+1);
  assert(readl(regs+0x100)==1 && get_unaligned_le32(memory+PE_PROBE_MAILBOX+372)==1 && get_unaligned_le32(memory+PE_PROBE_MAILBOX+352));
  assert(!ni_uplink_stop(&t) && !t.ni.uplink_memory && !t.ni.uplink_ready && releases==before_free+2);
  assert(readl(regs+0x108)==7 && readl(regs+0x10c)==0x1234);cases++;
 }
 unsigned after_fault_alloc=allocations,after_fault_free=releases;
 for(unsigned vp=9;vp<=10;vp++)for(unsigned original=0;original<4;original++){
 memset(memory,0,sizeof(memory));memset(regs,0,sizeof(regs));
 struct rtl_ax_tx t={.rx=memory,.ni={.vp=regs+0x100,.dma=regs,.vp_index=vp}};
 u32 saved=0xa5555555,mask=3U<<(vp*2);saved=(saved&~mask)|(original<<(vp*2));writel(saved,regs+0x14);
 writel(7,regs+0x108);writel(0x1234,regs+0x10c);
 assert(!ni_uplink_start(&t)&&!paused);
 u32 selected=readl(regs+0x14);assert((selected&~mask)==(saved&~mask));
 assert((selected&mask)==0);
 /* ARM may retain an old prefix while PE writes the correct one to DDR.
  * Pattern1 ReadOnce observes that old line; pattern0 bypasses the snoop. */
 u8 cached[12]={0xa7,0x75,0x9a,0xf2,5,0xe4,0xaa,0xaa,3,0,0,0};
 u8 ddr[12]={0xd0,0x5b,0xcb,0xd3,0xeb,0xb2,0x3a,0x2e,0xa7,0x75,0x9a,0xf2};
 const u8*seen=((selected>>(vp*2))&3)==1?cached:ddr;
 assert(!memcmp(seen,ddr,12));assert(readl(regs+0x108)==2);
 /* The other radio may change its selector before this radio stops. */
 u32 other=3U<<((vp==9?10:9)*2), updated=selected^other;
 writel(updated,regs+0x14);
 /* A previous reload still owns its queue-base registers on timeout. */
 load_fail=true;put_unaligned_le32(BIT(31),regs+0x104);unsigned writes=base_writes,frees=releases;
 assert(ni_uplink_stop(&t)==-ETIMEDOUT&&t.ni.uplink_memory&&releases==frees&&base_writes==writes);
 assert(!get_unaligned_le32(memory+PE_PROBE_MAILBOX+352)&&!get_unaligned_le32(memory+PE_PROBE_MAILBOX+372));
 load_fail=false;put_unaligned_le32(0,regs+0x104);
 assert(!ni_uplink_stop(&t)&&!paused&&!t.ni.uplink_memory);
 assert(readl(regs+0x14)==((updated&~mask)|(saved&mask))&&readl(regs+0x108)==7&&readl(regs+0x10c)==0x1234);
 }
 assert(allocations==after_fault_alloc+8&&releases==after_fault_free+8);printf("Uplink readiness: %u failed-start/stop/retry/enable scenarios passed\n",cases);puts("NI uplink start/stop: both radios, preserved unrelated VP modes, no stale CCI header, exact restore PASS");
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(code)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
