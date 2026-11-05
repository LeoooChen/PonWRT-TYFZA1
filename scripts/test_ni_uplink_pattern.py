#!/usr/bin/env python3
"""Execute NI start/stop register programming and the PE/CCI stale-header model."""
from pathlib import Path
import subprocess,tempfile,argparse
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path);args=parser.parse_args()
source=(args.source or root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h').read_text()
a=source.index('static int ni_uplink_stop(');b=source.index('\nstatic bool ni_epps_empty',a)
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
struct rtl_ax_tx {void *dma_device,*rx,*hw;struct {void *uplink_memory,*vp,*dma;u32 uplink_dma,vp_saved[5],vp_index;}ni;};
static u8 memory[0x20000],regs[0x1000];static bool paused;static unsigned allocations,releases;
static void *rtl_ax_rx_pe_memory(void *p){assert(p==memory);return p;}
static u32 get_unaligned_le32(const void*p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void*p){memcpy(p,&v,4);}
static u32 readl(void*p){u32 v=get_unaligned_le32(p);if((u8*)p==regs+0x104)v&=~(BIT(31)|BIT(30));return v;}
static void writel(u32 v,void*p){put_unaligned_le32(v,p);}
#define readl_poll_timeout(a,v,c,d,t) ({v=readl(a);(c)?0:-ETIMEDOUT;})
static void dma_wmb(void){}
static int rtl_ax_tx_pause(struct rtl_ax_tx*t){(void)t;paused=true;return 0;}
static void rtl_ax_tx_open(struct rtl_ax_tx*t){(void)t;paused=false;}
static void *dma_alloc_coherent(void*d,unsigned n,u32*a,int f){(void)d;(void)f;assert(paused&&n==8192);*a=0x5000000;allocations++;return calloc(1,n);}
static void dma_free_coherent(void*d,unsigned n,void*p,u32 a){(void)d;(void)n;(void)a;assert(paused);releases++;free(p);}
static void rtl_ax_rx_uplink_peer(void*r,unsigned id,void*h){(void)r;(void)h;assert(id==3&&paused);}
'''
code+=source[a:b]
code+=r'''
int main(void){
 for(unsigned vp=9;vp<=10;vp++)for(unsigned original=0;original<4;original++){
 memset(memory,0,sizeof(memory));memset(regs,0,sizeof(regs));
 struct rtl_ax_tx t={.rx=memory,.ni={.vp=regs+0x100,.dma=regs,.vp_index=vp}};
 u32 saved=0xa5555555,mask=3U<<(vp*2);saved=(saved&~mask)|(original<<(vp*2));writel(saved,regs+0x14);
 writel(7,regs+0x108);writel(0x1234,regs+0x10c);
 assert(!ni_uplink_start(&t,3)&&!paused);
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
 assert(!ni_uplink_stop(&t)&&!paused&&!t.ni.uplink_memory);
 assert(readl(regs+0x14)==((updated&~mask)|(saved&mask))&&readl(regs+0x108)==7&&readl(regs+0x10c)==0x1234);
 }
 assert(allocations==8&&releases==8);puts("NI uplink start/stop: both radios, preserved unrelated VP modes, no stale CCI header, exact restore PASS");
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(code)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
