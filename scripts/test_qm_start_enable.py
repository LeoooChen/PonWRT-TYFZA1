#!/usr/bin/env python3
"""Exercise actual QM startup enable suffix and shutdown before published-ready."""
import argparse
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');a=p.parse_args()
relative='port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c'
source=subprocess.check_output(['git','show',a.revision+':'+relative],cwd=root,text=True) if a.revision else (root/relative).read_text()
start=source.index('\t/* Stock aal_l3_te_init starts CB scanning')
end=source.index('\nstatic void rtl_qm_rx_retire(',start)
tail=source[start:end];tail=tail[:tail.index('\nvoid rtl_qm_rx_free(')]
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define CB_CTRL 0x950c
#define RMU_CTRL 0x6104
#define ES_CTRL 0x6108
#define RTL_QM_VOQS 8
struct rtl_qm_rx {void *ni;bool deepq_configured,hardware_owned;int stop_error,fttr_rx[8];};
static u32 regs[65536/4];static unsigned fault,close_fault,writes;
static u32 readl(void *p){return *(u32*)p;}
static void writel(u32 v,void *p){
 unsigned reg=(char*)p-(char*)regs;writes++;
 unsigned selected=reg==CB_CTRL?1:reg==0x9004?2:reg==ES_CTRL?3:reg==0x610c?4:reg==RMU_CTRL?5:0;
 bool enable=selected==4?(v&65535):selected==2?(v&4):(v&BIT(31));
 if((enable&&fault==selected)||(!enable&&close_fault==selected))return;
 *(u32*)p=v;
}
static void rtl_fttr_rx_reset(int *r){*r=0;}
static int start_tail(struct rtl_qm_rx *rx){void *ni=rx->ni;
'''+tail+r'''
int main(void){unsigned cases=0;
 for(unsigned failure=0;failure<=5;failure++)for(unsigned close=0;close<2;close++){
  memset(regs,0,sizeof(regs));regs[CB_CTRL/4]=0x23456789;regs[RMU_CTRL/4]=0x07654321;
  struct rtl_qm_rx rx={.ni=regs,.hardware_owned=true};fault=failure;close_fault=0;writes=0;
  assert(start_tail(&rx)==(failure?-EIO:0));
  assert(rx.deepq_configured==!failure&&rx.hardware_owned);
  if(failure)assert(writes==failure);
  if(close){regs[CB_CTRL/4]|=BIT(31);close_fault=1;}
  rtl_qm_rx_stop(&rx);assert(!rx.deepq_configured&&rx.hardware_owned&&rx.stop_error==(close?-EIO:0));
  fault=close_fault=0;rtl_qm_rx_stop(&rx);assert(!rx.stop_error);
  assert(!start_tail(&rx)&&rx.deepq_configured);
  assert((regs[ES_CTRL/4]&0xb300ffffU)==(0x8462ff01U&0xb300ffffU));
  cases++;
 }
 puts("QM startup enables:12 write-failure/cleanup-failure/retry cases; no false ready publication PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='qm-start-enable-') as tmp:
    d=Path(tmp);(d/'test.c').write_text(code)
    subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(d/'test.c'),'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
