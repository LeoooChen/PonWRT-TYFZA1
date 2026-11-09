#!/usr/bin/env python3
"""Execute actual NI preparation across all-COS backlog and partial setup faults."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');a=p.parse_args()
source=subprocess.check_output(['git','show',a.revision+':'+str((driver/'ni-test.h').relative_to(root))],cwd=root,text=True) if a.revision else (driver/'ni-test.h').read_text()
def function(name):
    m=re.search(r'^static (?:int|bool) '+name+r'\([^;]*?\)\s*\{',source,re.M)
    start,end,depth=m.start(),m.end(),1
    while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]+'\n'
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define PE_PROBE_MAILBOX 0x2000
#define RING_BYTES 32768
#define BUFFER_COUNT 2496
#define RTL9617C_WIFI_BID_START 1536
#define RTL9617C_WIFI_EPP_SHIFT 15
#define EPP_CONSUMERS 0x11c00
#define RTL_AX_TX_PUBLISH_WRITE 193
#define RTL_AX_TX_PUBLISH_READ 194
#include "tx-ownership.h"
#include "linux/rtl9617c-qm-layout.h"
#define lower_32_bits(v) ((u32)(v))
#define pr_info(...) ((void)0)
struct rtl_ax_tx {void *rx;struct {void *memory;u32 dma;unsigned slot;}pe;
 struct {bool active;void *regs,*dma,*vp;unsigned cpu,eq,profile,vp_index,registers[14],saved[14],arb[8],attrs[2][2],epp_bases[8],epp_eq[8];}ni;};
static u32 regs[65536/4],dma[4096/4];static u8 memory[0x80000];static u32 pe_tx_program[1];
static unsigned maps,writes,accesses,fail_access,event_calls,rpq_calls,late,eq_errors;
static int fail_map,fail_event,fail_rpq;
static struct rtl_ax_tx *active;
static void *ioremap(uint64_t address,unsigned size){
 maps++;if((int)maps==fail_map)return NULL;
 assert((address==0x4f4300000ULL&&size==65536)||(address==0x4f7001000ULL&&size==4096));
 return address==0x4f4300000ULL?(void*)regs:(void*)dma;
}
static u32 readl(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void writel(u32 v,void *p){writes++;memcpy(p,&v,4);}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static void dma_wmb(void){}
static int ni_access(struct rtl_ax_tx *tx,unsigned reg,u32 request){
 accesses++;
 if(request&BIT(30)){assert(reg==0x1654);writes++;}
 else {
  if(reg==0x1654)regs[0x1658/4]=0xa000|(request&511);
  else {assert(reg==0x66a4);regs[0x66ac/4]=0xb000|(request&63);regs[0x66a8/4]=0xc000|(request&63);}
 }
 if(late){regs[(0x7000+tx->ni.cpu*32)/4+late-1]+=8;late=0;}
 return accesses==fail_access?-ETIMEDOUT:0;
}
static int attr_write(struct rtl_ax_tx *tx,unsigned index,const u32 *data){
 (void)tx;(void)index;(void)data;writes++;return eq_errors?-EIO:0;
}
static int pe_event_prepare(struct rtl_ax_tx *tx){assert(tx==active);event_calls++;return fail_event;}
static int rtl_ax_rx_pe_rpq_start(void *rx,void *m,u32 dma_address,const u32 *program,unsigned bytes){
 (void)rx;assert(m==memory&&dma_address==0x5000000&&program==pe_tx_program&&bytes==4);rpq_calls++;return fail_rpq;
}
'''+''.join(function(n) for n in ('ni_es_empty','ni_epps_empty','ni_test_prepare'))+r'''
static struct rtl_ax_tx setup(unsigned slot){
 memset(regs,0,sizeof(regs));memset(dma,0,sizeof(dma));memset(memory,0xa5,sizeof(memory));
 maps=writes=accesses=fail_access=event_calls=rpq_calls=late=eq_errors=0;fail_map=fail_event=fail_rpq=0;
 struct rtl_ax_tx tx={.pe={.memory=memory,.dma=0x5000000,.slot=slot},.ni={.cpu=3-slot,.eq=6+slot,.profile=7-slot,.vp_index=9+slot}};
 tx.ni.registers[0]=0x61e8+tx.ni.eq*20;
 for(unsigned i=1;i<5;i++)tx.ni.registers[i]=tx.ni.registers[0]+i*4;
 tx.ni.registers[5]=0x6128+tx.ni.profile*4;tx.ni.registers[6]=0x6148+tx.ni.cpu*4;
 tx.ni.registers[7]=0x61c8+tx.ni.cpu*4;tx.ni.registers[8]=0x6558+tx.ni.cpu*4;
 tx.ni.registers[9]=0x657c+tx.ni.profile*4;tx.ni.registers[10]=0x65a4+tx.ni.cpu*32;
 tx.ni.registers[11]=0x7100+tx.ni.cpu*32;tx.ni.registers[12]=0x7200+tx.ni.cpu*32;tx.ni.registers[13]=0x6108;
 regs[tx.ni.registers[0]/4]=1;regs[(tx.ni.registers[0]+4)/4]=BUFFER_COUNT<<16|(RTL9617C_WIFI_BID_START+slot*BUFFER_COUNT);
 for(unsigned cos=0;cos<8;cos++){
  regs[(0x7000+tx.ni.cpu*32)/4+cos]=regs[tx.ni.registers[11]/4+cos]=cos*16;
  regs[tx.ni.registers[12]/4+cos]=0x8000000+cos*32768;
  regs[tx.ni.registers[10]/4+cos]=cos+32;
 }
 return tx;
}
int main(void){unsigned cases=0;
 for(unsigned slot=0;slot<2;slot++)for(unsigned cos=0;cos<8;cos++)for(unsigned kind=0;kind<3;kind++){
  struct rtl_ax_tx tx=setup(slot);active=&tx;
  if(kind==0)regs[(0x7000+tx.ni.cpu*32)/4+cos]+=8;
  if(kind==1)regs[0x6830/4]=BIT(tx.ni.cpu*8+cos);
  if(kind==2)regs[0x6108/4]=BIT(tx.ni.cpu);
  assert(ni_test_prepare(&tx,1)==-EBUSY&&!tx.ni.active&&!writes&&!accesses&&!event_calls);
  for(unsigned i=0;i<sizeof(memory);i++)assert(memory[i]==0xa5);
  cases++;
 }
 for(unsigned slot=0;slot<2;slot++)for(unsigned fail=0;fail<=14;fail++){
  struct rtl_ax_tx tx=setup(slot);active=&tx;
  if(fail<=10)fail_access=fail;
  else if(fail==11)eq_errors=1;
  else if(fail==12)fail_access=11; /* first arbitration write */
  else if(fail==13)fail_event=-EIO;
  else fail_rpq=-EIO;
  int ret=ni_test_prepare(&tx,1);
  assert(ret==(fail?fail<=10||fail==12?-ETIMEDOUT:-EIO:0));
  assert(tx.ni.active==(!fail||fail>10));
  for(unsigned cos=0;cos<8;cos++){
   assert(tx.ni.epp_bases[cos]==0x8000000+cos*32768&&tx.ni.epp_eq[cos]==cos+32);
   assert(regs[tx.ni.registers[11]/4+cos]==cos*16);
  }
  if(fail&&fail<=10){assert(!writes);for(unsigned i=0;i<sizeof(memory);i++)assert(memory[i]==0xa5);}
  unsigned old=maps;assert(ni_test_prepare(&tx,1)==-EBUSY&&maps==old);cases++;
 }
 for(unsigned slot=0;slot<2;slot++)for(unsigned cos=0;cos<8;cos++){
  struct rtl_ax_tx tx=setup(slot);active=&tx;late=cos+1;
  assert(!ni_test_prepare(&tx,1));
  assert(regs[(0x7000+tx.ni.cpu*32)/4+cos]==cos*16+8);
  assert(regs[tx.ni.registers[11]/4+cos]==cos*16);
  assert(*(u32*)(memory+EPP_CONSUMERS+cos*4)==cos*16);cases++;
 }
 for(unsigned fail=1;fail<=2;fail++){
  struct rtl_ax_tx tx=setup(0);active=&tx;fail_map=fail;
  assert(ni_test_prepare(&tx,1)==-ENOMEM&&!tx.ni.active&&!writes);
  unsigned old=maps;assert(ni_test_prepare(&tx,1)==-EBUSY&&maps==old);cases++;
 }
 printf("NI prepare: %u both-radio/all-COS ownership, late publication, partial snapshot/setup and retained mapping cases PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='ni-prepare-') as temp:
    d=Path(temp);(d/'test.c').write_text(code)
    subprocess.run(['cc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined',
                    '-I'+str(driver),'-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(d/'test.c'),'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
