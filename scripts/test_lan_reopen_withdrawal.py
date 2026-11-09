#!/usr/bin/env python3
"""Execute LAN open/stop with failed FE withdrawal and shared-engine ownership."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');a=p.parse_args()
relative='port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c'
source=subprocess.check_output(['git','show',a.revision+':'+relative],cwd=root,text=True) if a.revision else (root/relative).read_text()
def fn(name):
 start=re.search(r'^static int '+name+r'\([^;]*?\)\s*\{',source,re.M).start();end=source.index('{',start)+1;depth=1
 while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]+'\n'
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define PORT_STATIC 0xa400
#define PORT_RX 0xa5f8
#define PORT_TX 0xa5fc
#define PORT_STRIDE 0x90
#define spin_lock_irqsave(l,f) do{(void)(l);(f)=0;}while(0)
#define spin_unlock_irqrestore(l,f) do{(void)(l);(void)(f);}while(0)
struct net_device {void *private,*phydev;unsigned char dev_addr[6];};
struct rtl_xram {void *fe;bool dma_tx;unsigned users;int tx_lock,dma;};
struct rtl_lan {struct rtl_xram *shared;unsigned port;};
static unsigned regs[65536/4],phy_started,queue_started,forgotten,flushes,gets,puts_count;static int flush_error,start_error;
static void *netdev_priv(struct net_device *d){return d->private;}
static int rtl_engine_get(struct rtl_xram *p){gets++;if(start_error)return start_error;p->users++;return 0;}
static void rtl_engine_put(struct rtl_xram *p){assert(p->users);p->users--;puts_count++;}
static void rtl_update(struct rtl_xram *p,unsigned reg,u32 mask,u32 value){(void)p;regs[reg/4]=(regs[reg/4]&~mask)|(value&mask);}
static int rtl_fe_flush_port(void *fe,struct net_device *d){(void)fe;(void)d;flushes++;return flush_error;}
static void phy_start(void *p){(void)p;phy_started++;}static void phy_stop(void *p){(void)p;assert(phy_started);phy_started--;}
static void netif_start_queue(struct net_device *d){(void)d;queue_started++;}static void netif_stop_queue(struct net_device *d){(void)d;assert(queue_started);queue_started--;}
static void rtl_dma_tx_forget(int *dma,struct net_device *d){(void)dma;(void)d;forgotten++;}
'''+fn('rtl_open')+fn('rtl_stop')+r'''
int main(void){unsigned cases=0;
 for(unsigned port=0;port<5;port++)for(unsigned other=0;other<2;other++){
  struct rtl_xram p={.users=other,.dma_tx=true};struct rtl_lan lan={&p,port};struct net_device d={.private=&lan};
  flush_error=start_error=0;assert(!rtl_open(&d)&&p.users==other+1&&phy_started==1&&queue_started==1);
  flush_error=-ETIMEDOUT;unsigned saved=forgotten;
  int stop_result=rtl_stop(&d);
  assert(p.users==other&&!phy_started&&!queue_started&&forgotten==saved+1);
  assert(!(regs[(PORT_RX+port*PORT_STRIDE)/4]&1)&&!(regs[(PORT_TX+port*PORT_STRIDE)/4]&1));
  for(unsigned attempt=0;attempt<3;attempt++){
   assert(rtl_open(&d)==-ETIMEDOUT&&p.users==other&&!phy_started&&!queue_started);
   assert(!(regs[(PORT_RX+port*PORT_STRIDE)/4]&1)&&!(regs[(PORT_TX+port*PORT_STRIDE)/4]&1));
  }
  assert(stop_result==-ETIMEDOUT);
  flush_error=0;assert(!rtl_open(&d)&&p.users==other+1&&phy_started==1&&queue_started==1);
  assert(!rtl_stop(&d)&&p.users==other&&!phy_started&&!queue_started);
  unsigned before=flushes;start_error=-EIO;
  assert(rtl_open(&d)==-EIO&&flushes==before&&p.users==other);cases++;
 }
 puts("LAN reopen:10 five-port/shared-engine cases; failed withdrawal keeps MAC closed, balances refs and retries before reopening PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='lan-reopen-') as tmp:
 d=Path(tmp);(d/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(d/'test.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True)
