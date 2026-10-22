#!/usr/bin/env python3
"""Execute the native PE start/stop sequence and preserve the other core."""
from pathlib import Path
import re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
p=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/pe-core.c'
s=p.read_text()
def function(name):
 a=re.search(r'^.*\b'+name+r'\([^;]*?\)\s*\{',s,re.M).start();b=s.index('{',a)+1;depth=1
 while depth:depth+=(s[b]=='{')-(s[b]=='}');b+=1
 return s[a:b]+'\n'
common=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define __iomem
struct pe_probe {unsigned core;u32 reset_mask;bool configured;void *reset,*ram,*atu;u32 entries[6][7],staging[8],dma;};
static u32 reset=0xffffffff,ram[2],atu[2][8],bist=0xffffffff;
static unsigned delays,transactions;
static u32 readl(void *p){return *(u32 *)p;}
static void writel(u32 v,void *p){*(u32 *)p=v;}
static void udelay(unsigned us){assert(us==1000);delays++;}
static void *ioremap(uintptr_t a,unsigned bytes){assert(a==0xf4320004&&bytes==4);return &bist;}
static void iounmap(void *p){assert(p==&bist);}
static int pe_probe_access(struct pe_probe *p,u32 cmd){assert((cmd&BIT(31))&&(cmd&63)==63);assert(p->core<2);unsigned index=(cmd>>24)&7;if(index==0||index==5){assert(((u32 *)p->atu)[1]==(index?0x0fc0:0x1fc0));assert(((u32 *)p->atu)[2]==0xffff);assert(((u32 *)p->atu)[3]==p->dma>>16);}transactions++;return 0;}
'''
test=r'''
int main(void){
 struct pe_probe p[2]={0};
 for(unsigned i=0;i<2;i++){p[i].core=i;p[i].reset_mask=BIT(18+i)|BIT(20+i);p[i].reset=&reset;p[i].ram=&ram[i];p[i].atu=atu[i];p[i].dma=0x04000000+i*0x20000;}
 assert(!pe_probe_core_start(&p[1]));
 assert(!(reset&p[1].reset_mask)&&(reset&p[0].reset_mask)==p[0].reset_mask);
 u32 running1=reset;
 assert(!pe_probe_core_start(&p[0]));
 assert(!(reset&(p[0].reset_mask|p[1].reset_mask)));
 assert((running1&~p[0].reset_mask)==reset&&transactions==12&&delays==4);
 assert(!(bist&BIT(18)));
 pe_probe_core_stop(&p[0]);
 assert((reset&p[0].reset_mask)==p[0].reset_mask&&!(reset&p[1].reset_mask));
 assert((ram[0]&BIT(5))&&!(ram[1]&BIT(5)));
 pe_probe_core_stop(&p[1]);
 assert((reset&(p[0].reset_mask|p[1].reset_mask))==(p[0].reset_mask|p[1].reset_mask));
 assert((reset&~(p[0].reset_mask|p[1].reset_mask))==(0xffffffff&~(p[0].reset_mask|p[1].reset_mask)));
}
'''
with tempfile.TemporaryDirectory(prefix='pe-core-reset-',dir=root/'artifacts') as tmp:
 p=Path(tmp);c=p/'case.c';c.write_text(common+''.join(function(n) for n in ('pe_probe_reset','pe_probe_core_stop','pe_probe_core_start'))+test)
 subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(c),'-o',str(p/'case')],check=True)
 subprocess.run([str(p/'case')],check=True)
print('Actual native start/stop: independent core reset and SRAM sleep, unrelated core bits preserved.')
