#!/usr/bin/env python3
"""Test DMA-backed skb replacement with allocation and mapping failures."""
from pathlib import Path
import re,subprocess,json
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'artifacts/test-rx-skb-ownership'
p.mkdir(exist_ok=True)
s=(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rx.c').read_text();a=s.index('static int rtl_ax_rx_buffer_alloc(');b=s.index('\nstatic bool rtl_ax_rx_drain(',a);functions=s[a:b]
common=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
typedef uint32_t u32;typedef uint64_t u64;typedef uintptr_t dma_addr_t;typedef int gfp_t;
#define NET_SKB_PAD 32
#define GFP_ATOMIC 0
#define DMA_BIDIRECTIONAL 3
#define DMA_ATTR_SKIP_CPU_SYNC 32
#define READ_ONCE(x) (x)
#define WRITE_ONCE(p,v) ((p)=(v))
#define cpu_to_le32(v) ((u32)(v))
struct device {int unused;};
struct sk_buff {unsigned char *head,*data;unsigned len;};
struct rtl_ax_rx_buffer {unsigned char *data;dma_addr_t dma;struct sk_buff *skb;};
struct rtl_ax_rx_queue {struct rtl_ax_rx_buffer payloads[4];u32 addresses[4];unsigned slot_ids[4];unsigned consumer,buffer_bytes;u64 skb_transfers,skb_prepare_ns;};
struct rtl_ax_rx {struct device *pe_device;int irq_lock;struct rtl_ax_rx_queue queues[2];};
struct rtl_ax_rx_packet {const unsigned char *payload;unsigned bytes;};
static bool rtl_ax_datapath_profile=true;
static unsigned fail_alloc,fail_map,freed,unmaps,locks,unlocks;
static dma_addr_t unmapped;
#define spin_lock_irqsave(lock,flags) do {assert(!*(lock));*(lock)=1;(flags)=0;locks++;}while(0)
#define spin_unlock_irqrestore(lock,flags) do {assert(*(lock));assert(!(flags));*(lock)=0;unlocks++;}while(0)
static u64 ktime_get_ns(void){static u64 t;t+=100;return t;}
static void put_unaligned_le32(unsigned v,void *p){memcpy(p,&v,4);}
static struct sk_buff *alloc_skb(unsigned n,gfp_t flags){(void)flags;if(fail_alloc)return NULL;struct sk_buff *s=calloc(1,sizeof(*s));assert(s);s->head=malloc(n);assert(s->head);memset(s->head,0xa5,n);s->data=s->head;return s;}
static void skb_reserve(struct sk_buff *s,unsigned n){s->data+=n;}
static void skb_put(struct sk_buff *s,unsigned n){s->len+=n;}
static void kfree_skb(struct sk_buff *s){freed++;free(s->head);free(s);}
static dma_addr_t dma_map_single(struct device *d,void *data,unsigned n,int dir){(void)d;assert(n==12288&&dir==3);unsigned v;memcpy(&v,data,4);assert(v==0);return fail_map?0:(uintptr_t)data;}
static bool dma_mapping_error(struct device *d,dma_addr_t dma){(void)d;return !dma;}
static void dma_unmap_single_attrs(struct device *d,dma_addr_t dma,unsigned n,int dir,unsigned attrs){(void)d;assert(n==12288&&dir==3&&attrs==32);unmapped=dma;unmaps++;}
'''
test=r'''
int main(void){
 for(unsigned mode=0;mode<3;mode++){
  struct device dev={0};struct rtl_ax_rx rx={.pe_device=&dev};struct rtl_ax_rx_queue *q=&rx.queues[0];q->consumer=2;q->slot_ids[2]=1;q->buffer_bytes=12288;
  fail_alloc=fail_map=0;
  assert(!rtl_ax_rx_buffer_alloc(&rx,12288,0,&q->payloads[1]));
  struct rtl_ax_rx_buffer old=q->payloads[1];struct rtl_ax_rx_packet packet={old.data+36,6000};
  freed=unmaps=locks=unlocks=0;fail_alloc=mode==1;fail_map=mode==2;
  struct sk_buff *skb=rtl_ax_rx_take_skb(&rx,&packet);
  if(mode==0){assert(skb==old.skb&&skb->data==packet.payload&&skb->len==6000);assert(unmaps==1&&unmapped==old.dma&&locks==1&&unlocks==1&&!freed);
   assert(q->payloads[1].skb!=old.skb&&q->payloads[1].data==q->payloads[1].skb->data&&q->skb_transfers==1&&q->skb_prepare_ns==100);
   assert(q->addresses[1]==(u32)q->payloads[1].dma&&q->addresses[2]==0);
   kfree_skb(q->payloads[1].skb);
  }else{assert(!skb&&!unmaps&&!locks&&!unlocks&&q->payloads[1].skb==old.skb&&q->payloads[1].dma==old.dma&&old.skb->data==old.data&&old.skb->len==0&&q->skb_transfers==0);assert(freed==(mode==2));}
  for(unsigned i=36;i<6036;i++)assert(old.data[i]==0xa5);
  kfree_skb(old.skb);
 }
}
'''
(p/'check-skb-handoff.c').write_text(common+functions+test)
subprocess.run(['gcc','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'check-skb-handoff.c'),'-o',str(p/'check-skb-handoff')],check=True)
subprocess.run([str(p/'check-skb-handoff')],check=True)
(p/'skb-handoff-check.json').write_text(json.dumps({'actual_functions':True,'cases':['replacement is DMA mapped before old skb handoff; swap under IRQ lock','allocation failure preserves old mapped skb','DMA mapping failure frees only new skb'],'old_payload_unchanged':True,'scope':'Three focused C ownership cases; kernel DMA and skb allocators are mocked, not hardware-emulated.'},indent=2)+'\n')
print('Three DMA-backed skb ownership cases passed.')
