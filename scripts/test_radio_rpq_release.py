#!/usr/bin/env python3
"""Check RPQ drain ordering with a mocked outstanding-read model, not hardware."""
from pathlib import Path
import subprocess,tempfile,argparse
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--before',action='store_true')
a=parser.parse_args()
s=(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rx.c').read_text()
start=s.index('static void rtl_ax_rx_pe_drain(');at=s.index('{',start)+1;depth=1
while depth:
 depth+=(s[at]=='{')-(s[at]=='}');at+=1
f=s[start:at]
if a.before:f=f.replace('dma_mb();','dma_wmb();')
code=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint32_t u32;
#define RTL_AX_RPQ_BUFFER_BYTES 1024
struct rtl_ax_rx_packet {bool pe_completed;unsigned id;};
struct rtl_ax_rx {u8 *rpq_memory,*pe_forward;u32 dfs_epoch[4];};
static u8 memory[0x3000],reports[256*1024];
static unsigned pending_reads,acquired,dispatched,returned,parsed,invalid,initial,delayed;
static bool grow;
static u32 get_unaligned_le32(const void*p){u32 v;memcpy(&v,p,4);return v;}
static void raw_put(u32 v,void*p){memcpy(p,&v,4);}
static void dma_rmb(void){acquired++;}
static void __attribute__((unused)) dma_wmb(void){}
static void __attribute__((unused)) dma_mb(void){pending_reads=0;}
static bool rtl_ax_rx_epoch_old(u32 *epoch,u32 id){(void)epoch;return id&1;}
static int rtl_ax_receive_packet(u8 *p,unsigned bytes,struct rtl_ax_rx_packet *pkt){
 assert(bytes==1024&&pkt->pe_completed&&acquired==parsed+1);
 unsigned id=(p-reports)/1024;assert(id==((initial+returned)&255));
 parsed++;pending_reads++;pkt->id=id;
 return invalid&&parsed%3==0?-1:0;
}
static void rtl_ax_rx_dispatch(struct rtl_ax_rx *rx,bool old,struct rtl_ax_rx_packet *pkt){
 assert(rx->pe_forward==reports&&old==!!(pkt->id&1));dispatched++;
}
static void put_unaligned_le32(u32 v,void*p){
 assert(p==memory+0x2000+296&&v==((initial+returned+1)&255));
 /* A store-only barrier leaves an earlier DMA read free to overlap reuse. */
 assert(!pending_reads&&"RPQ buffer returned before outstanding reads are ordered");
 returned++;raw_put(v,p);
 if(grow&&delayed){
  u32 producer=get_unaligned_le32(memory+0x2000+292);
  raw_put((producer+1)&255,memory+0x2000+292);delayed--;
 }
}
'''+f+r'''
int main(void){unsigned cases=0;
 for(initial=0;initial<256;initial++)for(unsigned n=0;n<=65;n++)for(invalid=0;invalid<2;invalid++){
  memset(memory,0,sizeof(memory));raw_put(initial,memory+0x2000+296);raw_put((initial+n)&255,memory+0x2000+292);
  struct rtl_ax_rx rx={.rpq_memory=memory,.pe_forward=reports};
  pending_reads=acquired=dispatched=returned=parsed=0;grow=false;
  rtl_ax_rx_pe_drain(&rx);unsigned want=n<64?n:64;
  assert(returned==want&&parsed==want&&dispatched==want-(invalid?want/3:0));cases++;
 }
 for(initial=0;initial<256;initial++){
  memset(memory,0,sizeof(memory));raw_put(initial,memory+0x2000+296);raw_put((initial+1)&255,memory+0x2000+292);
  struct rtl_ax_rx rx={.rpq_memory=memory,.pe_forward=reports};
  pending_reads=acquired=dispatched=returned=parsed=0;invalid=0;grow=true;delayed=80;
  rtl_ax_rx_pe_drain(&rx);assert(returned==64&&delayed==16);cases++;
 }
 printf("Actual RPQ drain: %u acquire/read/release, malformed, budget, wrap and moving-producer cases PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='rpq-release-') as tmp:
 p=Path(tmp);(p/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
