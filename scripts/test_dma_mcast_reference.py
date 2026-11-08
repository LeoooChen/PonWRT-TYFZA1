#!/usr/bin/env python3
"""Exercise multicast DMA copy/reference lifetime at actual descriptor boundaries."""
from pathlib import Path
import argparse,re,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path);args=p.parse_args()
b=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source=(args.source or b/'rtl9617c-dma.c').read_text();header=(b/'rtl9617c-dma.h').read_text()
def function(text,name):
 a=re.search(r'^.*\b'+name+r'\([^;]*?\)\s*\{',text,re.M).start();end=text.index('{',a)+1;depth=1
 while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[a:end]+'\n'
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32,__le32;typedef uint64_t u64;typedef uintptr_t dma_addr_t;typedef int netdev_tx_t;
#define __iomem
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define max(a,b) ((a)>(b)?(a):(b))
#define cpu_to_le32(v) (v)
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
struct net_device {bool stopped;struct {unsigned tx_packets,tx_bytes,tx_dropped;}stats;};
struct sk_buff {unsigned len,stamp;bool consumed,bad_copy,change_in_copy;unsigned char data[2048];};
struct rtl9617c_ni_frame {unsigned prefix_length,header_high,header_low;bool raw_payload;const void *prefix;};
static unsigned generation=7,references,releases,submitted,barriers,reads,reset_done;
static uint8_t registers[0x1000];static bool resetting;
static int rtl9617c_bridge_mcast_tx_stamp(const struct sk_buff*s,u32 *stamp){*stamp=s->stamp;return s->stamp&&s->stamp!=generation?-ESTALE:0;}
static bool rtl9617c_bridge_mcast_tx_get(u32 stamp){if(!stamp)return true;if(stamp!=generation)return false;references++;return true;}
static void rtl9617c_bridge_mcast_tx_put(bool held){if(held){assert(references);assert(!resetting||reset_done);references--;releases++;}}
static u32 readl(void *p){u32 v;memcpy(&v,p,4);reads++;if(p==registers+0xf00 && resetting)reset_done=1;return v;}
static void writel(u32 v,void *p){
 if(p==registers+0x618){assert(barriers);submitted++;}
 if(p==registers){if(v&0x100)resetting=true;}
 memcpy(p,&v,4);
}
static void put_unaligned_le32(u32 v,void*p){memcpy(p,&v,4);}
static void udelay(unsigned n){assert(n==10);}
#define dma_wmb() (barriers++)
static bool netif_queue_stopped(struct net_device*d){return d->stopped;}
static void netif_stop_queue(struct net_device*d){d->stopped=true;}
static void netif_wake_queue(struct net_device*d){d->stopped=false;}
static int skb_copy_bits(struct sk_buff*s,unsigned offset,void*p,unsigned len){
 if(s->change_in_copy)generation++;
 if(s->bad_copy)return -EFAULT;
 assert(offset+len<=s->len);memcpy(p,s->data+offset,len);return 0;
}
static void dev_consume_skb_any(struct sk_buff*s){assert(!s->consumed);s->consumed=true;}
static void dev_kfree_skb_any(struct sk_buff*s){assert(!s->consumed);s->consumed=true;}
'''
code+=header[header.index('#define RTL_DMA_DEPTH'):header.index('/* HP=11')]
code+='\n'.join(l for l in source.splitlines() if l.startswith('#define '))+'\n'
code+=function(header,'rtl_dma_tx_available')
for name in ('rtl_dma_vp','rtl_dma_tx_reset','rtl_dma_tx_stop','rtl_dma_tx_complete','rtl_dma_tx_forget','rtl_dma_tx_frame'):code+=function(source,name)
code+=r'''
static void rp(unsigned ptr){put_unaligned_le32(ptr,registers+0x61c);}
int main(void){
 static uint8_t memory[MEMORY_BYTES];struct net_device dev={};unsigned cases=0;
 for(unsigned start=0;start<64;start++)for(unsigned count=1;count<=12;count++){
  struct rtl_dma_tx tx={.vp=8,.registers=registers,.memory=memory,.descriptors=(void*)memory,.memory_dma=0x04000000,.read=start,.write=start};
  references=releases=submitted=barriers=reset_done=0;resetting=false;generation=7;memset(registers,0,sizeof(registers));memset(memory,0,sizeof(memory));rp(start);
  for(unsigned i=0;i<count;i++){
   struct sk_buff s={.len=64,.stamp=generation};memset(s.data,i,sizeof(s.data));
   assert(!rtl_dma_tx_frame(&tx,&s,&dev,NULL)&&s.consumed);
   assert(references==i+1 && tx.multicast_held[(start+i)&63]);
  }
  assert(submitted==count);generation++; /* Writer closes learned lookup. */
  rtl_dma_tx_forget(&tx,&dev);assert(references==count); /* netdev refs != payload refs */
  for(unsigned fetched=0;fetched<=count;fetched++){
   rp((start+fetched)&63);rtl_dma_tx_complete(&tx,&dev);
   unsigned done=fetched>2?fetched-2:0;
   assert(references==count-done && releases==done && tx.read==((start+done)&63));
   rtl_dma_tx_complete(&tx,&dev);assert(releases==done);
  }
  assert(references==(count<2?count:2));
  /* The final two owners are not released by equal RP/WP or a port close.
   * The existing paired DMA+AFT reset is a separate ownership boundary. */
  rtl_dma_tx_stop(&tx);assert(!references && releases==count && reset_done);
  rtl_dma_tx_stop(&tx);assert(releases==count);
  for(unsigned i=0;i<64;i++)assert(!tx.multicast_held[i]);
  cases++;
 }
 /* Normal software traffic advances past the final two retained copies;
  * no fake descriptors or generated packets are needed to prove completion. */
 for(unsigned start=0;start<64;start++){
  struct rtl_dma_tx tx={.vp=8,.registers=registers,.memory=memory,.descriptors=(void*)memory,.read=start,.write=start};
  memset(registers,0,sizeof(registers));generation=7;references=releases=0;resetting=false;rp(start);
  for(unsigned i=0;i<3;i++){struct sk_buff s={.len=64,.stamp=7};assert(!rtl_dma_tx_frame(&tx,&s,&dev,NULL));}
  rp(tx.write);rtl_dma_tx_complete(&tx,&dev);assert(references==2&&releases==1);
  generation++;
  for(unsigned i=0;i<2;i++){
   struct sk_buff s={.len=64};assert(!rtl_dma_tx_frame(&tx,&s,&dev,NULL));
   rp(tx.write);rtl_dma_tx_complete(&tx,&dev);assert(references==1-i);
  }
  assert(releases==3);rtl_dma_tx_stop(&tx);assert(releases==3);cases++;
 }
 for(unsigned mode=0;mode<4;mode++){
  struct rtl_dma_tx tx={.vp=8,.registers=registers,.memory=memory,.descriptors=(void*)memory};
  memset(registers,0,sizeof(registers));references=releases=submitted=barriers=0;generation=7;resetting=false;
  struct sk_buff s={.len=64,.stamp=7};
  if(mode==0)s.stamp=6;
  if(mode==1)s.bad_copy=true;
  if(mode==2)s.change_in_copy=true;
  if(mode==3)tx.write=61;
  assert(rtl_dma_tx_frame(&tx,&s,&dev,NULL)==(mode==3?NETDEV_TX_BUSY:NETDEV_TX_OK));
  assert(s.consumed==(mode!=3) && !references&&!releases&&!submitted&&!barriers);
  cases++;
 }
 printf("DMA multicast:%u wrap/prefetch/reset/error cases; skb disposal and owner-forget never release pending payload references\n",cases);
}
'''
with tempfile.TemporaryDirectory() as directory:
 p=Path(directory);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
