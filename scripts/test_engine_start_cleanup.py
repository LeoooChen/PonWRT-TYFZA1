#!/usr/bin/env python3
"""Execute shared-engine startup/refcount across DMA/QM and cleanup failures."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');a=p.parse_args()
relative='port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c'
source=subprocess.check_output(['git','show',a.revision+':'+relative],cwd=root,text=True) if a.revision else (root/relative).read_text()
def function(name):
 start=re.search(r'^static int '+name+r'\([^;]*?\)\s*\{',source,re.M).start();end=source.index('{',start)+1;depth=1
 while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]+'\n'
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define PORT_STRIDE 0x90
#define GLB_STATIC 0xc
#define PORT_TO_CPU 0xf
#define RX_ADDR 0x200
#define TX_ADDR 0x204
#define XRAM_CFG 0x20c
#define RX_PROMISC BIT(12)
#define CPU_DISABLE BIT(4)
#define RTL_XRAM_RX_END 1024
#define RTL_XRAM_TX_END 2048
#define RTL_XRAM_TX_START 1024
#define HRTIMER_MODE_REL 0
#define ms_to_ktime(n) (n)
#define spin_lock_irqsave(l,f) do{(void)(l);(f)=0;}while(0)
#define spin_unlock_irqrestore(l,f) do{(void)(l);(void)(f);}while(0)
struct net_device {int unused;};
struct dma {int reset_error;bool enabled;};
struct qm {int stop_error;bool enabled;};
struct rtl_xram {struct net_device *netdev,*ports[5],*fttr;void *ni;bool dma_tx,qm_rx,rx_active,tx_pending;
 struct {bool configured;}p6;struct dma dma;struct qm qm;unsigned port,tx_length,engine_users;int napi,timer,rx_lock,lan4_ready;};
static unsigned starts,stops,napi,timers,waits,sequence[8],seq_count;
static int dma_error,qm_error,stop_dma_error,stop_qm_error;
static u32 readl(void *p){return *(u32*)p;}static void writel(u32 v,void *p){*(u32*)p=v;}
static void rtl_update(struct rtl_xram *p,unsigned reg,u32 mask,u32 value){writel((readl(p->ni+reg)&~mask)|(value&mask),p->ni+reg);}
static void udelay(unsigned n){assert(n==100);}
static void rtl_mac_address(struct rtl_xram *p){assert(!p->engine_users);}
static void rtl_rx_mode(struct net_device *d){assert(d);}
static void napi_enable(int *n){(void)n;napi++;}
static void hrtimer_start(int *t,int delay,int mode){(void)t;assert(delay==1&&!mode);timers++;}
static void wait_for_completion(int *c){(void)c;waits++;}
static int rtl_dma_tx_start(struct dma *d){sequence[seq_count++]=1;starts++;d->enabled=true;return dma_error;}
static void rtl_dma_tx_stop(struct dma *d){sequence[seq_count++]=3;stops++;d->reset_error=stop_dma_error;if(!stop_dma_error)d->enabled=false;}
static int rtl_qm_rx_start(struct qm *q,unsigned port){assert(!port);sequence[seq_count++]=2;starts++;q->enabled=true;return qm_error;}
static __attribute__((unused)) void rtl_qm_rx_stop(struct qm *q){sequence[seq_count++]=4;stops++;q->stop_error=stop_qm_error;if(!stop_qm_error)q->enabled=false;}
'''+function('rtl_engine_start')+function('rtl_engine_get')+r'''
int main(void){unsigned cases=0;static u32 regs[65536/4];struct net_device dev={};
 for(unsigned mode=1;mode<4;mode++)for(unsigned failure=1;failure<=2;failure++)for(unsigned close=0;close<2;close++){
  if(!(mode&failure))continue;
  memset(regs,0,sizeof(regs));starts=stops=napi=timers=waits=seq_count=0;
  dma_error=failure==1?-EIO:0;qm_error=failure==2?-ETIMEDOUT:0;
  stop_dma_error=close?-EIO:0;stop_qm_error=close?-EIO:0;
  struct rtl_xram p={.netdev=&dev,.ports={&dev},.fttr=&dev,.ni=regs,.dma_tx=!!(mode&1),.qm_rx=!!(mode&2)};
  assert(rtl_engine_get(&p)==(failure==1?-EIO:-ETIMEDOUT));
  assert(!p.engine_users&&!p.rx_active&&!napi&&!timers&&waits==1);
  if(failure==1){assert(seq_count==2&&sequence[0]==1&&sequence[1]==3);assert(p.dma.enabled==!!close);}
  else {unsigned base=mode&1?1:0;assert(seq_count==(mode&1?4:2));assert(sequence[base]==2&&sequence[base+1]==4);if(mode&1)assert(sequence[base+2]==3);assert(p.qm.enabled==!!close);}
  dma_error=qm_error=stop_dma_error=stop_qm_error=0;seq_count=0;
  assert(!rtl_engine_get(&p)&&p.engine_users==1&&napi==1&&timers==1);
  unsigned before=starts;assert(!rtl_engine_get(&p)&&p.engine_users==2&&starts==before&&waits==2);cases++;
 }
 puts("Shared engine:8 partial-start/close-failure scenarios; cleanup order, original errors, zero failed references and successful retry PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='engine-start-') as tmp:
 d=Path(tmp);(d/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(d/'test.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True)
