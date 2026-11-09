#!/usr/bin/env python3
"""Run the production forced EPP drain against exact, separated QM allocations."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

r=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--revision');args=parser.parse_args()
s=(r/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c').read_text()
if args.revision:s=subprocess.check_output(['git','show',args.revision+':port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c'],cwd=r,text=True)
helper=s[s.index('int rtl_qm_wifi_retire('):]
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;typedef uint64_t dma_addr_t;
#define BIT(n) (1U<<(n))
#define RTL_QM_PE_POOLS 2
#define RTL_QM_PE_CHUNKS 3
#define RTL_QM_PE_EQ 6
#define RTL_QM_BUFFER_BYTES 2048
#define RTL9617C_WIFI_EPP_BYTES 32768
#define ES_CTRL 0x6108
#define EPP_BASE 0x7200
#define EPP_WRITE 0x7000
#define EPP_READ 0x7100
#define lower_32_bits(v) ((u32)(v))
#define dma_rmb() ((void)0)
struct rtl_qm_rx {
 void *ni,*wifi_epp[2],*wifi_memory[2][3];dma_addr_t wifi_epp_dma[2],wifi_dma[2][3];
 bool hardware_owned;
 struct {u32 address,next,cos;}wifi_retire[2];
};
static u32 regs[65536/4];static unsigned char rings[2][8*32768];
static struct rtl_qm_rx rx;static unsigned cpu,pool,returned,writes,ready_count,limit,rd_fail,late,eq_credit,eq_valid;
static u32 addresses[128];
static unsigned int rtl_qm_wifi_chunk_bytes(unsigned n){return (n==2?448:1024)*2048;}
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static u32 readl(const void *p){
 if(p==regs+(0x6328+(6+pool)*4)/4)return (eq_valid?BIT(31):0)|eq_credit;
 if(p==regs+(0x6368+cpu*8)/4){ready_count++;return returned<limit?BIT(31):0;}
 return *(const u32*)p;
}
static void writel(u32 v,void *p){
 writes++;
 unsigned offset=(u32*)p-regs;
 if(offset>=(EPP_READ+cpu*32)/4 && offset<(EPP_READ+cpu*32+32)/4){if(rd_fail)return;*(u32*)p=v;return;}
 assert(offset==(0x636c+cpu*8)/4 && returned<limit);
 assert((v&127)==6+pool);u32 address=v&~127U;
 for(unsigned i=0;i<returned;i++)assert(addresses[i]!=address);
 assert(rx.wifi_retire[pool].address==address);
 unsigned cos=rx.wifi_retire[pool].cos;
 assert(regs[(EPP_READ+cpu*32)/4+cos]==rx.wifi_retire[pool].next);
 assert(eq_valid&&eq_credit);eq_credit--;
 addresses[returned++]=address;
 /* EQ may reuse this slot immediately after PUSH. A blocked retry must
  * retain the sampled address rather than reread this descriptor. */
 unsigned old=(rx.wifi_retire[pool].next-8)&32767;
 memset(rings[pool]+cos*32768+old,0xcc,8);
 if(late){regs[(EPP_WRITE+cpu*32)/4]=8;late=0;}
}
#define readl_poll_timeout(p,v,c,d,t) ({(void)(d);(void)(t);v=readl(p);(c)?0:-ETIMEDOUT;})
static void setup(unsigned selected){
 memset(&rx,0,sizeof(rx));memset(regs,0,sizeof(regs));memset(rings,0,sizeof(rings));
 pool=selected;cpu=3-pool;returned=writes=ready_count=rd_fail=late=0;limit=128;eq_credit=128;eq_valid=1;
 rx.ni=regs;rx.hardware_owned=true;
 for(unsigned p=0;p<2;p++){
  rx.wifi_epp[p]=rings[p];rx.wifi_epp_dma[p]=0x8000000+p*0x100000;
  for(unsigned c=0;c<3;c++){rx.wifi_dma[p][c]=0x4000000+p*0x1000000+c*0x400000;rx.wifi_memory[p][c]=&rx;}
  for(unsigned c=0;c<8;c++)regs[(EPP_BASE+(3-p)*32)/4+c]=rx.wifi_epp_dma[p]+c*32768;
 }
}
static void entry(unsigned cos,unsigned offset,unsigned n,unsigned flags){
 u32 address=rx.wifi_dma[pool][n%3]+(n/3)*2048;
 memcpy(rings[pool]+cos*32768+offset,&address,4);
 ((u32*)(rings[pool]+cos*32768+offset))[0]|=flags;
}
'''+helper+r'''
int main(void){unsigned cases=0;
 for(unsigned p=0;p<2;p++)for(unsigned cos=0;cos<8;cos++)for(unsigned valid=0;valid<2;valid++){
  setup(p);entry(cos,0,0,0x50);entry(cos,8,1,0x50);regs[(EPP_WRITE+cpu*32)/4+cos]=16;
  eq_valid=valid;eq_credit=valid?0:9;
  assert(rtl_qm_wifi_retire(&rx,p)==-ETIMEDOUT && !returned && rx.wifi_retire[p].address);
  assert(regs[(EPP_READ+cpu*32)/4+cos]==8);
  memset(rings[p]+cos*32768,0xcc,8);
  eq_valid=1;eq_credit=1;
  assert(rtl_qm_wifi_retire(&rx,p)==-ETIMEDOUT&&returned==1&&rx.wifi_retire[p].address);
  assert(regs[(EPP_READ+cpu*32)/4+cos]==16&&!eq_credit);
  eq_credit=1;assert(!rtl_qm_wifi_retire(&rx,p)&&returned==2&&!rx.wifi_retire[p].address);cases++;
 }

 for(unsigned p=0;p<2;p++)for(unsigned cos=0;cos<8;cos++)for(unsigned wrap=0;wrap<2;wrap++)for(unsigned flags=0;flags<4;flags++){
  setup(p);unsigned start=wrap?32760:0;entry(cos,start,0,(unsigned[]){0,0x10,0x40,0x50}[flags]);entry(cos,(start+8)&32767,1,0x50);
  regs[(EPP_READ+cpu*32)/4+cos]=start;regs[(EPP_WRITE+cpu*32)/4+cos]=(start+16)&32767;
  limit=0;assert(rtl_qm_wifi_retire(&rx,p)==-ETIMEDOUT&&!returned&&rx.wifi_retire[p].address);
  assert(regs[(EPP_READ+cpu*32)/4+cos]==((start+8)&32767));
  memset(rings[p]+cos*32768+start,0xee,8);
  limit=1;assert(rtl_qm_wifi_retire(&rx,p)==-ETIMEDOUT&&returned==1);
  limit=2;assert(!rtl_qm_wifi_retire(&rx,p)&&returned==2&&!rx.wifi_retire[p].address);
  assert(!rtl_qm_wifi_retire(&rx,p)&&returned==2);cases++;
 }
 for(unsigned p=0;p<2;p++)for(unsigned fault=0;fault<8;fault++){
  setup(p);entry(0,0,0,0x50);regs[(EPP_WRITE+cpu*32)/4]=8;
  int error=-EBUSY;
  if(fault==0)regs[ES_CTRL/4]=BIT(cpu);
  if(fault==1)regs[0x6830/4]=BIT(cpu*8);
  if(fault==2){regs[(EPP_BASE+cpu*32)/4]^=4096;error=-ESTALE;}
  if(fault==3){regs[(EPP_WRITE+cpu*32)/4]=9;error=-EPROTO;}
  if(fault==4){regs[(EPP_READ+cpu*32)/4]=32768;error=-EPROTO;}
  if(fault==5){((u32*)rings[p])[0]=rx.wifi_dma[1-p][0]|0x50;error=-ERANGE;}
  if(fault==6){((u32*)rings[p])[0]+=128;error=-ERANGE;}
  if(fault==7){rd_fail=1;error=-EIO;}
  assert(rtl_qm_wifi_retire(&rx,p)==error&&!returned);
  if(fault==7){rd_fail=0;assert(!rtl_qm_wifi_retire(&rx,p)&&returned==1);}
  else assert(!writes);
  cases++;
 }
 for(unsigned p=0;p<2;p++){
  setup(p);for(unsigned n=0;n<65;n++)entry(4,n*8,n,0x50);regs[(EPP_WRITE+cpu*32)/4+4]=65*8;
  assert(rtl_qm_wifi_retire(&rx,p)==-EAGAIN&&returned==64);
  assert(!rtl_qm_wifi_retire(&rx,p)&&returned==65);cases++;
  setup(p);entry(0,0,0,0x50);entry(7,0,1,0x50);regs[(EPP_WRITE+cpu*32)/4+7]=8;late=1;
  assert(rtl_qm_wifi_retire(&rx,p)==-EAGAIN&&returned==1);
  assert(!rtl_qm_wifi_retire(&rx,p)&&returned==2);cases++;
 }

 for(unsigned p=0;p<2;p++)for(unsigned chunk=0;chunk<3;chunk++)for(unsigned outside=0;outside<2;outside++){
  setup(p);u32 address=rx.wifi_dma[p][chunk]+rtl_qm_wifi_chunk_bytes(chunk)-(outside?0:2048);
  ((u32*)rings[p])[0]=address|0x50;regs[(EPP_WRITE+cpu*32)/4]=8;
  assert(rtl_qm_wifi_retire(&rx,p)==(outside?-ERANGE:0));assert(returned==!outside);cases++;
 }
 for(unsigned p=0;p<2;p++){
  setup(p);entry(5,0,0,0x50);regs[(EPP_WRITE+cpu*32)/4+5]=8;limit=0;
  assert(rtl_qm_wifi_retire(&rx,p)==-ETIMEDOUT && rx.wifi_retire[p].address);
  unsigned before=writes;u32 base=regs[(EPP_BASE+cpu*32)/4+5];regs[(EPP_BASE+cpu*32)/4+5]^=4096;
  limit=1;assert(rtl_qm_wifi_retire(&rx,p)==-ESTALE&&!returned&&writes==before);
  regs[(EPP_BASE+cpu*32)/4+5]=base;assert(!rtl_qm_wifi_retire(&rx,p)&&returned==1);cases++;
 }
 setup(0);assert(rtl_qm_wifi_retire(&rx,2)==-EINVAL);rx.hardware_owned=false;assert(rtl_qm_wifi_retire(&rx,0)==-ENODEV);
 printf("QM forced EPP return: %u pool/COS/wrap/fragment/credit/write/geometry/budget/late-arrival cases PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='qm-wifi-retire-') as temp:
    d=Path(temp);(d/'test.c').write_text(code)
    subprocess.run(['cc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(d/'test.c'),'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
