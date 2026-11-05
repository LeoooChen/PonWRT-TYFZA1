#!/usr/bin/env python3
"""Run actual QM polling on fragmented P6 frames and execute stock segment limits."""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--ni', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
args = p.parse_args()
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X7, UC_ARM64_REG_X21, UC_ARM64_REG_X25, UC_ARM64_REG_PC
with args.ni.open('rb') as stream:
    elf = ELFFile(stream)
    symbol = elf.get_section_by_name('.symtab').get_symbol_by_name('ca_ni_rx_napi')[0]
    code = elf.get_section(symbol['st_shndx']).data()
u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
base, stack = 0x1000000, 0x2000000
u.mem_map(base, (len(code)+4095)&~4095);u.mem_write(base,code);u.mem_map(stack,4096)
u.reg_write(UC_ARM64_REG_SP,stack)
limits = []
for full in (0,1):
    u.reg_write(UC_ARM64_REG_X1,0);u.mem_write(stack+332,(1996).to_bytes(4,'little'))
    u.emu_start(base+(0xcf0 if full else 0xcd0),base+0xd04,count=20)
    assert u.reg_read(UC_ARM64_REG_PC)==base+0xd04
    limits.append(u.reg_read(UC_ARM64_REG_X7))
assert limits==[1592,1584]
for length in (1,404,412,1599,1600,1601):
    u.reg_write(UC_ARM64_REG_X0,length);u.reg_write(UC_ARM64_REG_X21,length)
    u.emu_start(base+0x1168,base+0x1178,count=10)
    assert u.reg_read(UC_ARM64_REG_PC)==base+0x1178
    assert u.reg_read(UC_ARM64_REG_X25)==min(length,1600)

# Reuse the established Linux API shim, replacing only ring/payload/allocation boundaries.
tree = ast.parse((root/'scripts/test_ni_rx_credit.py').read_text())
common = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
              and any(isinstance(t,ast.Name) and t.id=='common' for t in n.targets))
common = common.replace('u8 storage[128]', 'u8 storage[2048]')
common = common.replace('struct rtl_qm_rx {', '''
#include <errno.h>
static struct sk_buff *napi_alloc_skb(struct napi_struct *,unsigned);
static void *skb_put(struct sk_buff *,unsigned);
static void dev_kfree_skb_any(struct sk_buff *);
#include "rtl9617c-fttr-rx.h"
struct rtl_qm_rx {struct rtl_fttr_rx fttr_rx[8];unsigned fttr_epoch;
''')
common = common.replace('struct rtl9617c_ni_endpoint *pon;', 'struct rtl9617c_ni_endpoint *pon,*fttr;')
start = common.index('static u64 ktime_get_ns(')
common = common[:start]+r'''
#define CB_CTRL 0x950c
#define RMU_CTRL 0x6104
#define ES_CTRL 0x6108
static u64 clock_ns;
static unsigned live,allocations,received_count,returned[512],expected_bytes,fail_alloc;
static u8 expected[2048],device_ring[8192];
static u64 ktime_get_ns(void){return clock_ns;}
static u32 readl(void *p){u32 v;memcpy(&v,p,4);return v;}
static void writel(u32 v,void *p){memcpy(p,&v,4);}
static void dma_sync_single_for_cpu(void *d,dma_addr_t a,unsigned n,int direction){
 (void)d;assert(direction==DMA_FROM_DEVICE);
 if(a>=active->ring_dma && a<active->ring_dma+8192){
  unsigned off=a-active->ring_dma;assert(!(off%64)&&!(n%64)&&off+n<=8192);
  memcpy(active->ring_memory+off,device_ring+off,n);
 }else assert(n==2048&&a>=active->memory_dma+8192&&a+2048<=active->memory_dma+RTL_QM_MEMORY_BYTES);
}
static int rtl_qm_try_push(void *context,unsigned address,unsigned eq){
 struct rtl_qm_rx *rx=context;unsigned id=(address-rx->memory_dma-8192)/2048;
 assert(id<512 && (eq==4 || eq==5));returned[id]++;return 0;
}
static bool rtl_rx_checksum_valid(u32 c,const void *p,unsigned n){(void)c;(void)p;(void)n;return false;}
static struct sk_buff *napi_alloc_skb(struct napi_struct *n,unsigned size){
 (void)n;assert(size<=1996);if(fail_alloc)return NULL;
 struct sk_buff *s=calloc(1,sizeof(*s));assert(s);s->data=s->storage;live++;allocations++;return s;
}
static void *skb_put(struct sk_buff *s,unsigned n){void *p=s->data+s->len;assert(s->len+n<=sizeof(s->storage));s->len+=n;return p;}
static void dev_kfree_skb_any(struct sk_buff *s){assert(live);live--;free(s);}
static bool netif_running(struct net_device *d){return d->running;}
static unsigned eth_type_trans(struct sk_buff *s,struct net_device *d){(void)d;s->data+=14;s->len-=14;return ETH_P_IP;}
static void napi_gro_receive(struct napi_struct *n,struct sk_buff *s){(void)n;dev_kfree_skb_any(s);}
static void p6_receive(struct rtl9617c_ni_endpoint *ep,struct napi_struct *n,struct sk_buff *s,unsigned source){
 (void)ep;(void)n;assert(source==(6|123<<8));assert(s->len==expected_bytes);
 assert(!memcmp(s->data,expected,s->len));assert(!s->ip_summed);received_count++;dev_kfree_skb_any(s);
}
static void entry(struct rtl_qm_rx *rx,unsigned cos,unsigned slot,unsigned id,unsigned flags,unsigned full,unsigned length,unsigned start){
 unsigned offset=8192+id*2048;u8 *data=rx->memory+offset+RTL_QM_HEADROOM;
 memset(rx->memory+offset,0xcd,2048);
 if(flags&16){unsigned h=full?16:8;
  put(__builtin_bswap32((123U<<18)|(full?BIT(31):0)),data);
  put(__builtin_bswap32(((length+(full?8:0))<<15)|(6<<9)),data+4);
  memcpy(data+h,expected,1600-h);
 }else memcpy(data,expected+start,length-start);
 put((rx->memory_dma+offset)|flags|(id<256?4:5),device_ring+cos*1024+slot*8);
 put(0,device_ring+cos*1024+slot*8+4);
 writel(((slot+1)%128)*8,rx->ni+EPP_WRITE+cos*4);
}
'''
common=common.replace('bool profile;', 'bool profile,deepq_configured;')
driver = root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (driver/'rtl9617c-qm.c').read_text()
poll = source[source.index('static void rtl_qm_rx_retire('):source.index('\nssize_t rtl_qm_rx_state(')]
stop = source[source.index('void rtl_qm_rx_stop('):source.index('\nvoid rtl_qm_rx_free(')]
cases = r'''
int main(void){
 unsigned cases=0;
 for(unsigned full=0;full<2;full++)for(unsigned cos=0;cos<8;cos++)for(unsigned wrap=0;wrap<2;wrap++)
 for(unsigned length_case=0;length_case<3;length_case++)for(unsigned fault=0;fault<8;fault++) {
  struct net_device owner={.running=true};struct napi_struct napi={.dev=&owner};
  struct rtl9617c_ni_endpoint p6={.receive=p6_receive};
  struct rtl_qm_rx rx={.memory_dma=0x4000000,.ring_dma=0x6000000,.device=&owner,.fttr=&p6};active=&rx;
  rx.memory=calloc(1,RTL_QM_MEMORY_BYTES);rx.ring_memory=calloc(1,8192);rx.ni=calloc(1,65536);
  memset(returned,0,sizeof(returned));received_count=allocations=0;clock_ns=1;fail_alloc=fault==1;
  unsigned cap=1600-(full?16:8);
  expected_bytes=fault==2?1997:length_case==0?cap+1:length_case==1?cap+4:1996;
  for(unsigned i=0;i<2048;i++)expected[i]=(i*19+i/7)&255;
  unsigned first=wrap?127:0,second=(first+1)%128;
  rx.read[cos]=first*8;
  entry(&rx,cos,first,0,16,full,expected_bytes,0);
  assert(rtl_qm_rx_poll(&rx,&napi,1)==1 && returned[0]==1 && !received_count);
  /* Original DMA bytes may be reused before the next EPP appears. */
  memset(rx.memory+8192,0xee,2048);
  if(fault==3)clock_ns+=1000000001ULL;
  if(fault==4)rx.fttr_epoch++;
  if(fault==5){rtl_qm_rx_stop(&rx);assert(!live);}
  if(fault==6)rx.fttr=NULL;
  entry(&rx,cos,second,256,fault==7?0:64,full,expected_bytes,cap);
  assert(rtl_qm_rx_poll(&rx,&napi,1)==1 && returned[256]==1);
  assert(received_count==(fault==0) && !live);
  if(fault && fault!=6)assert(owner.stats.rx_errors || owner.stats.rx_dropped);
  if(fault==6)assert(owner.stats.rx_dropped==1);
  rtl_qm_rx_stop(&rx);free(rx.memory);free(rx.ring_memory);free(rx.ni);cases++;
 }
 printf("P6 RX: %u actual poll cases, boundary/max payloads, both header modes, 8 COS, EPP wrap, delayed publication, early refill, failures, timeout/epoch/stop passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='fttr-rx-') as temp:
    out=Path(temp);(out/'test.c').write_text(common+poll+stop+cases)
    subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',
                    '-fsanitize=address,undefined','-I'+str(driver),
                    '-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),
                    str(out/'test.c'),'-o',str(out/'test')],check=True)
    subprocess.run([str(out/'test')],check=True)
args.output.write_text(json.dumps({'stock_first_payload':limits,'stock_continuation':1600,'native_cases':768,
    'scope':'stock ARM64 segment-size instructions and actual native QM poll with modeled DMA/network; ASan/UBSan; no physical traffic'},indent=2)+'\n')
