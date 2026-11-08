#!/usr/bin/env python3
"""Exercise actual cached CPU EPP polling with independently published DMA bytes."""
from pathlib import Path
import argparse, ast, subprocess, tempfile
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path)
args=parser.parse_args()
source=(args.source or root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c').read_text()
tree=ast.parse((root/'scripts/test_ni_rx_credit.py').read_text())
common=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='common' for t in n.targets))
common=common.replace('static bool seen[129];', 'static bool seen[129];static u8 hardware_ring[8192];static unsigned syncs;')
common=common.replace('void *entry=active->ring_memory+(producer%128)*8;', 'void *entry=hardware_ring+(producer%128)*8;')
a=common.index('static void dma_sync_single_for_cpu(');b=common.index('static int rtl_qm_try_push',a)
common=common[:a]+r"""
static void dma_sync_single_for_cpu(void *d,dma_addr_t a,unsigned n,int direction){
 assert(d==active->device&&direction==DMA_FROM_DEVICE);
 if(a>=active->ring_dma&&a<active->ring_dma+8192){
  unsigned off=a-active->ring_dma;assert(n&&!(off%64)&&!(n%64)&&off+n<=8192);
  memcpy(active->ring_memory+off,hardware_ring+off,n);syncs++;
 }else assert(n==2048&&a>=active->memory_dma+8192);
}
"""+common[b:]
a=common.index('static int rtl_qm_try_push');b=common.index('static bool rtl_rx_checksum_valid',a)
common=common[:a]+r"""
static int rtl_qm_try_push(void *ctx,unsigned address,unsigned eq){
 struct rtl_qm_rx *rx=ctx;unsigned id=(address-(u32)rx->memory_dma-8192)/2048;
 assert(eq==4&&id<129);return 0;
}
"""+common[b:]
a=common.index('static void received(');b=common.index('static void napi_gro_receive',a)
common=common[:a]+r"""
static void received(struct sk_buff*s){u8 expected[64];unsigned skip=kind==0?14:0;
 unsigned id=get_unaligned_be32(s->data+42-skip);assert(id<129&&!seen[id]);
 packet_bytes(id,expected);assert(!memcmp(s->data,expected+skip,64-skip));seen[id]=true;delivered++;free(s);
}
"""+common[b:]
a=source.index('static void rtl_qm_rx_retire(');b=source.index('\nssize_t rtl_qm_rx_state(',a)
cases=r"""
int main(void){unsigned cases=0;
 for(unsigned start=0;start<128;start++)for(unsigned len=1;len<128;len+=7)for(unsigned budget=1;budget<=64;budget*=2){
  struct net_device owner={.running=true},input={.running=true};struct napi_struct napi={.dev=&owner};
  struct rtl_qm_rx rx={.memory_dma=0x4000000,.ring_dma=0x6000000,.device=&owner};rx.lan[0]=&input;
  rx.memory=calloc(1,RTL_QM_MEMORY_BYTES);rx.ring_memory=malloc(8192);memset(rx.ring_memory,0xcc,8192);rx.ni=calloc(1,65536);active=&rx;
  producer=consumer=start;rx.read[0]=start*8;delivered=drops=syncs=kind=0;memset(seen,0,sizeof(seen));
  for(unsigned i=0;i<len;i++)enqueue(i);
  for(unsigned i=0;consumer!=producer&&i<128;i++)rtl_qm_rx_poll(&rx,&napi,budget);
  assert(delivered==len&&rx.refill.returned==len&&!drops&&syncs);
  /* Hardware writes the next entry in a line already present in CPU cache. */
  enqueue(128);assert(rtl_qm_rx_poll(&rx,&napi,budget)==1&&seen[128]);
  free(rx.memory);free(rx.ring_memory);free(rx.ni);cases++;
 }
 printf("Actual cached EPP poll: %u start/length/budget/wrap cases; poisoned cache, DMA publication, partial-line extension and payloads passed\n",cases);
}
"""
drivers=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
with tempfile.TemporaryDirectory(prefix='cached-epp-', dir=root/'artifacts') as work:
 p=Path(work)
 (p/'test.c').write_text(common+source[a:b]+cases)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-misleading-indentation','-fsanitize=address,undefined','-I',str(drivers),'-I',str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)

# Execute the native stop and ring reinitialization across repeated opens.
start=source.index('\t/* Hardware write pointers survive ndo_stop/open;')
end=source.index('\n\trx->trace_count = 0;', start)+len('\n\trx->trace_count = 0;')
stop=source.index('void rtl_qm_rx_stop(')
stop_end=source.index('\nvoid rtl_qm_rx_free(', stop)
lifecycle=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define RTL_QM_RING_BYTES 1024
#define RTL_QM_VOQS 8
#define RTL_QM_MEMORY_BYTES 16384
#define DMA_FROM_DEVICE 2
#define EPP_WRITE 0x7000
#define EPP_READ 0x7100
#define CB_CTRL 0x950c
#define RMU_CTRL 0x6104
#define ES_CTRL 0x6108
struct rtl_qm_rx {void *ni,*device,*ring_memory,*memory;unsigned ring_dma,memory_dma,read[8],trace_count;bool deepq_configured;};
static unsigned char registers[65536],cpu[8192],hardware[8192];
static unsigned synced,retired,reordered;
static u32 readl(void *p){u32 v;memcpy(&v,p,4);return v;}
static void writel(u32 v,void *p){memcpy(p,&v,4);if(p>=((void *)registers+EPP_READ)&&p<((void *)registers+EPP_READ+32))retired++;}
static void rtl_qm_axi_reorder(struct rtl_qm_rx *rx){(void)rx;assert(retired==8);reordered++;}
static void dma_sync_single_for_device(void *d,unsigned address,unsigned bytes,int direction){
 (void)d;assert(direction==DMA_FROM_DEVICE&&reordered==1);
 if(address==0x6000000){assert(bytes==8192);for(unsigned i=0;i<bytes;i++)assert(cpu[i]==0);memcpy(hardware,cpu,bytes);synced++;}
 else assert(address==0x4000000&&bytes==RTL_QM_MEMORY_BYTES);
}
'''
lifecycle+=source[stop:stop_end]
lifecycle+='static void reopen(struct rtl_qm_rx *rx){void *ni=rx->ni;unsigned i;\n'+source[start:end]+'\n}\n'
lifecycle+=r'''
int main(void){
 struct rtl_qm_rx rx={.ni=registers,.ring_memory=cpu,.ring_dma=0x6000000,.memory_dma=0x4000000};
 for(unsigned epoch=0;epoch<4;epoch++){
  memset(cpu,0xcc+epoch,sizeof(cpu));memset(hardware,0x55+epoch,sizeof(hardware));
  for(unsigned i=0;i<8;i++){writel((epoch*31+i*13)%128*8,registers+EPP_WRITE+i*4);rx.read[i]=0;}
  writel(~0U,registers+CB_CTRL);writel(~0U,registers+RMU_CTRL);writel(~0U,registers+ES_CTRL);writel(~0U,registers+0x610c);
  rx.deepq_configured=true;rtl_qm_rx_stop(&rx);
  assert(!rx.deepq_configured&&!(readl(registers+CB_CTRL)&BIT(31))&&!(readl(registers+RMU_CTRL)&BIT(31))&&!(readl(registers+ES_CTRL)&BIT(31))&&!(readl(registers+0x610c)&0xffff));
  synced=retired=reordered=0;rx.trace_count=99;reopen(&rx);
  assert(synced==1&&rx.trace_count==0);
  for(unsigned i=0;i<8;i++)assert(rx.read[i]==readl(registers+EPP_WRITE+i*4)&&rx.read[i]==readl(registers+EPP_READ+i*4));
  for(unsigned i=0;i<sizeof(hardware);i++)assert(hardware[i]==0);
 }
 puts("Cached EPP lifecycle: native stop and repeated ring reinitialization retire all eight pointers and publish cleared cache lines");
}
'''
with tempfile.TemporaryDirectory(prefix='cached-epp-reopen-', dir=root/'artifacts') as work:
 p=Path(work);(p/'test.c').write_text(lifecycle)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
