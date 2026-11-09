#!/usr/bin/env python3
"""Execute native NI register restoration and release with indirect-IO failures."""
from pathlib import Path
import argparse,re,struct,subprocess,sys,tempfile
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--stock",type=Path);args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
if args.stock:
 sys.path.insert(0,str(root/'tools'))
 from arm64_module_oracle import ModuleOracle
 from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
 writes=[];reads=0;logs=0;complete=1
 def boundary(name,a):
  global logs
  assert name=='printk';logs+=1;return 0
 oracle=ModuleOracle(args.stock,['aal_l3qm_set_epp_axi_attrib'],boundary)
 mmio=0x6000000;oracle.uc.mem_map(mmio,0x10000)
 oracle.word(oracle.symbols['g_ne_reg_iobase'][0],mmio)
 oracle.uc.mem_write(oracle.symbols['ca_aal_debug'][0],bytes(4))
 def write(u,access,address,size,value,data):writes.append((address-mmio,value))
 def read(u,access,address,size,value,data):
  global reads
  reads+=1
  if reads==complete:
   v=struct.unpack('<I',u.mem_read(address,4))[0];u.mem_write(address,struct.pack('<I',v&~(1<<31)))
 oracle.uc.hook_add(UC_HOOK_MEM_WRITE,write,begin=mmio,end=mmio+0xffff)
 oracle.uc.hook_add(UC_HOOK_MEM_READ,read,begin=mmio+0x66a4,end=mmio+0x66a7)
 for entry in (6,7,50,51):
  for complete in (1,299,300,301):
   writes.clear();reads=logs=0
   oracle.call('aal_l3qm_set_epp_axi_attrib',entry,0x0a008060)
   assert writes[0]==(0x66ac,0x0a008060)
   assert writes[1][0]==0x66a4 and writes[1][1]&0xc000003f==0xc0000000|entry
   assert reads==min(complete,300) and logs==(complete>300)
 print('Original AXI attribute:16 entry/completion cases confirm write/access order and300-poll timeout')
s=(driver/'ni-test.h').read_text()
def fn(name):
 a=re.search(r'^static (?:int|void|bool) '+name+r'\([^;]*?\)\s*\{',s,re.M).start();b=s.index('{',a)+1;n=1
 while n:n+=(s[b]=='{')-(s[b]=='}');b+=1
 return s[a:b]+'\n'
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#include "tx-ownership.h"
#include "linux/rtl9617c-mcast-pe.h"
#define BIT(n) (1U<<(n))
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_TX_RINGS 6
#define TEST_MEMORY_BYTES 128
struct rtl_ax_tx {void *dma_device,*rx;unsigned producer[6];struct {void *memory;}pe;
 struct {void *route_dev,*peer_dev[128];unsigned char peer_wifi[128];bool active,vp_active;void *regs,*dma,*vp,*test_memory;u32 test_dma;int ps_work;
 unsigned cpu,eq,registers[14],saved[14],arb[8],attrs[2][2],epp_bases[8],epp_eq[8],vp_saved[5],vp_index;}ni;};
static u32 mmio[0x10000/4],dma[0x1000/4],arb[512],attr[64][2];static unsigned char memory[0x80000];
static unsigned accesses,fail_at,polls,mmio_writes,opens,released,paused;
static bool load_fail;static unsigned loading;
static u32 get_unaligned_le32(const void *p){u32 x;memcpy(&x,p,4);return x;}
static void put_unaligned_le32(u32 x,void *p){memcpy(p,&x,4);}
static u32 readl(const void *p){return get_unaligned_le32(p);}
static void writel(u32 v,void *p){
 mmio_writes++;
 if(p==mmio+0x1654/4 || p==mmio+0x66a4/4){
 assert((v&0xc0000000U)==0xc0000000U);
 /* Model even a timed-out command as applied: rollback must be idempotent. */
 if(p==mmio+0x1654/4)arb[v&511]=mmio[0x1658/4];
 else {attr[v&63][0]=mmio[0x66ac/4];attr[v&63][1]=mmio[0x66a8/4];}
 accesses++;put_unaligned_le32(accesses==fail_at?v:v&~BIT(31),p);return;}
 if(p==dma+0x104/4){loading++;put_unaligned_le32(load_fail?v:0,p);return;}
 put_unaligned_le32(v,p);
}
#define readl_poll_timeout(p,v,c,d,t) ({(void)(d);(void)(t);polls++;v=readl(p);(c)?0:-ETIMEDOUT;})
static bool ni_epps_empty(struct rtl_ax_tx *t){(void)t;return true;}
static int ni_uplink_stop(struct rtl_ax_tx *t){(void)t;return 0;}
static int rtl_ax_tx_pause(struct rtl_ax_tx *t){(void)t;paused++;return 0;}
static int rtl_ax_rx_pe_rpq_stop(void *r){(void)r;return 0;}
static void ni_ps_retire(struct rtl_ax_tx *t){(void)t;}
static void pe_event_release(struct rtl_ax_tx *t){(void)t;}
static void cancel_work_sync(void *w){(void)w;}
static void dma_wmb(void){}
static void rtl_ax_tx_open(struct rtl_ax_tx *t){(void)t;opens++;}
static void dma_free_coherent(void *d,unsigned n,void *p,unsigned a){(void)d;(void)n;(void)p;(void)a;released++;}
static void iounmap(void *p){assert(p);released++;}
'''
code+=r'''
#define DMA_FROM_DEVICE 1
#define WRITE_ONCE(v,x) ((v)=(x))
#define cpu_to_le32(v) (v)
#define le32_to_cpu(v) (v)
static int rtl_ax_tx_ni_route(struct rtl_ax_tx *t,void *dev,unsigned id){(void)t;(void)dev;(void)id;assert(0);return 0;}
static void dev_put(void *p){(void)p;assert(0);}
static void *phys_to_virt(u32 a){(void)a;assert(0);return NULL;}
static void dma_sync_single_for_cpu(void *dev,u32 a,unsigned bytes,unsigned dir){(void)dev;(void)a;(void)bytes;(void)dir;assert(0);}
'''
code+=fn('ni_peer_put')+fn('ni_access')+fn('attr_write')+fn('ni_restore_registers')+fn('ni_test_release')+fn('ni_test_remove')
code+=r'''
static struct rtl_ax_tx setup(unsigned cpu){
 memset(mmio,0,sizeof(mmio));memset(dma,0,sizeof(dma));memset(memory,0,sizeof(memory));
 accesses=mmio_writes=polls=opens=released=paused=loading=0;load_fail=false;
 struct rtl_ax_tx t={.pe={memory},.ni={.active=true,.regs=mmio,.dma=dma,.vp=(u8*)dma+0x100,.cpu=cpu,.eq=cpu==3?6:7}};
 for(unsigned i=0;i<13;i++){t.ni.registers[i]=0x6200+i*4;t.ni.saved[i]=0x123000+i;}
 t.ni.registers[13]=0x6108;t.ni.saved[13]=BIT(cpu)|1;
 t.ni.registers[10]=0x65a4+cpu*32;t.ni.registers[11]=0x7100+cpu*32;t.ni.registers[12]=0x7200+cpu*32;
 for(unsigned i=0;i<8;i++){t.ni.arb[i]=i+17;t.ni.epp_bases[i]=0x6000000+i*4096;t.ni.epp_eq[i]=i+37;}
 t.ni.attrs[0][0]=0xa1;t.ni.attrs[0][1]=0xb1;t.ni.attrs[1][0]=0xa2;t.ni.attrs[1][1]=0xb2;
 mmio[0x6108/4]=0xf;put_unaligned_le32(1,memory+0x2060);
 return t;
}
int main(void){unsigned cases=0;
 for(unsigned cpu=2;cpu<=3;cpu++)for(unsigned fail=1;fail<=10;fail++){
  struct rtl_ax_tx t=setup(cpu);fail_at=fail;
  int result=ni_test_release(&t);
  assert(result==-ETIMEDOUT && t.ni.active && t.ni.regs==mmio && !released && !opens);
  assert(!(mmio[0x6108/4]&BIT(cpu)) && (mmio[0x6108/4]&~BIT(cpu))==(0xf&~BIT(cpu)));
  assert(accesses==fail);
  /* Repeated attempts cannot overwrite a still-busy engine's staging data. */
  unsigned writes=mmio_writes;
  assert(ni_test_release(&t)==-ETIMEDOUT&&!released&&!opens);
  assert(accesses==fail && mmio_writes==writes+1); /* ES disable only */
  mmio[0x1654/4]=mmio[0x66a4/4]=0;fail_at=0;
  assert(!ni_test_release(&t)&&!t.ni.active&&!t.ni.regs&&released==2&&opens==1);
  assert(mmio[0x6108/4]==0xf);
  for(unsigned i=0;i<8;i++)assert(arb[i*64+16+cpu]==t.ni.arb[i]);
  assert(!memcmp(attr[t.ni.eq],t.ni.attrs[0],8)&&!memcmp(attr[48+cpu],t.ni.attrs[1],8));
  for(unsigned i=0;i<8;i++){assert(mmio[t.ni.registers[12]/4+i]==t.ni.epp_bases[i]);assert(mmio[t.ni.registers[10]/4+i]==t.ni.epp_eq[i]);}
  cases++;
 }
 /* A stalled VP reload must stop before NI restoration or allocation release. */
 struct rtl_ax_tx t=setup(3);t.ni.vp_active=true;load_fail=true;fail_at=0;
 assert(ni_test_release(&t)==-ETIMEDOUT&&loading==1&&!accesses&&!released&&!opens&&t.ni.vp_active);
 load_fail=false;dma[0x104/4]=0;assert(!ni_test_release(&t)&&released==2&&!t.ni.vp_active);cases++;
 for(unsigned cpu=2;cpu<=3;cpu++)for(unsigned fail=1;fail<=10;fail++) {
  struct rtl_ax_tx forced=setup(cpu);fail_at=fail;
  /* Force the PCIe-teardown fallback through retained NI ownership. */
  put_unaligned_le32(1,memory+0x2078);
  assert(ni_test_remove(&forced)==-ETIMEDOUT&&forced.ni.active&&!released&&!opens);
  assert(!(mmio[0x6108/4]&BIT(cpu))&&accesses==fail);
  unsigned count=accesses;
  assert(ni_test_remove(&forced)==-ETIMEDOUT&&accesses==count&&!released);
  mmio[0x1654/4]=mmio[0x66a4/4]=0;fail_at=0;
  assert(!ni_test_remove(&forced)&&!forced.ni.active&&released==2&&!opens);cases++;
 }
 t=setup(3);fail_at=0;put_unaligned_le32(1,memory+0x2078);load_fail=true;
 assert(ni_test_remove(&t)==-ETIMEDOUT&&loading==1&&!accesses&&!released);
 unsigned writes=mmio_writes;assert(ni_test_remove(&t)==-ETIMEDOUT&&loading==1&&mmio_writes==writes+3);
 load_fail=false;dma[0x104/4]=0;assert(!ni_test_remove(&t)&&released==2);cases++;
 printf("NI restore:%u indirect-write/VP-load failures, busy-engine retry, successful release and unrelated ES bits passed\n",cases);
}
'''
code=code.replace('typedef uint32_t u32;','typedef uint32_t u32;typedef uint8_t u8;')
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'t.c').write_text(code)
 subprocess.run(['cc','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(driver),'-I'+str(driver.parents[4]/'include'),str(p/'t.c'),'-o',str(p/'t')],check=True)
 subprocess.run([str(p/'t')],check=True)
