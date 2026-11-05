#!/usr/bin/env python3
"""Execute native RX skb mapping/allocation/free, including partial failures."""
from pathlib import Path
import re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=(driver/'rx.c').read_text()
def function(name):
 a=re.search(r'^.*\b'+name+r'\([^;]*?\)\s*\{',source,re.M).start();b=source.index('{',a)+1;depth=1
 while depth:depth+=(source[b]=='{')-(source[b]=='}');b+=1
 return source[a:b]+'\n'
common=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint32_t dma_addr_t;typedef int gfp_t;
#define GFP_KERNEL 0
#define DMA_BIDIRECTIONAL 3
#define NET_SKB_PAD 32
#define lower_32_bits(v) (v)
#define cpu_to_le32(v) (v)
#include "receive.h"
struct device {unsigned id;};
struct sk_buff {u8 *head,*data;};
struct rtl_ax_rx_buffer {u8 *data;dma_addr_t dma;struct sk_buff *skb;};
struct rtl_ax_rx_queue {u8 *descriptors,*buffers;dma_addr_t descriptor_dma,buffer_dma;struct rtl_ax_rx_buffer *payloads;u32 *addresses;dma_addr_t addresses_dma;u16 slot_ids[256];unsigned allocated_payloads,buffer_bytes;};
struct rtl_ax_rx {struct device *pe_device;struct rtl_ax_rx_queue queues[2];};
struct skb_record {struct sk_buff *skb;dma_addr_t dma;bool mapped,freed;};
struct coherent {void *cpu;dma_addr_t dma;unsigned bytes;bool freed;};
static struct skb_record skbs[600];static struct coherent coherent[5];
static unsigned nskb,ncoh,alloc_calls,map_calls,coh_calls,fail_alloc,fail_map,fail_coh,largest;static bool fail_meta;
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static u32 read32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static struct sk_buff *alloc_skb(unsigned bytes,gfp_t flags){(void)flags;assert(bytes==12288+32);if(++alloc_calls==fail_alloc)return NULL;struct sk_buff *s=calloc(1,sizeof(*s));assert(s);s->head=malloc(bytes);assert(s->head);memset(s->head,0xa5,bytes);s->data=s->head;skbs[nskb++]=(struct skb_record){.skb=s};return s;}
static void skb_reserve(struct sk_buff *s,unsigned bytes){s->data+=bytes;}
static dma_addr_t dma_map_single(struct device *dev,void *cpu,unsigned bytes,int dir){assert(dev->id==1&&bytes==12288&&dir==3&&read32(cpu)==0);map_calls++;for(unsigned i=0;i<nskb;i++)if(!skbs[i].freed&&skbs[i].skb->data==cpu){if(map_calls==fail_map)return ~0U;skbs[i].mapped=true;return skbs[i].dma=0x08000000+((i*73)%514)*65536;}assert(0);return 0;}
static bool dma_mapping_error(struct device *dev,dma_addr_t dma){(void)dev;return dma==~0U;}
static void dma_unmap_single(struct device *dev,dma_addr_t dma,unsigned bytes,int dir){assert(dev->id==1&&bytes==12288&&dir==3);for(unsigned i=0;i<nskb;i++)if(skbs[i].dma==dma&&skbs[i].mapped){skbs[i].mapped=false;return;}assert(0);}
static void kfree_skb(struct sk_buff *s){for(unsigned i=0;i<nskb;i++)if(skbs[i].skb==s&&!skbs[i].freed){assert(!skbs[i].mapped);skbs[i].freed=true;free(s->head);free(s);return;}assert(0);}
#define dev_kfree_skb_any kfree_skb
static void *dma_alloc_coherent(struct device *dev,unsigned bytes,dma_addr_t *dma,int flags){(void)flags;assert(dev->id==1&&bytes<=262144);if(++coh_calls==fail_coh)return NULL;void *p=malloc(bytes);assert(p);*dma=0x02000000+ncoh*0x100000;coherent[ncoh++]=(struct coherent){.cpu=p,.dma=*dma,.bytes=bytes};if(bytes>largest)largest=bytes;return p;}
static void dma_free_coherent(struct device *dev,unsigned bytes,void *p,dma_addr_t dma){assert(dev->id==1);for(unsigned i=0;i<ncoh;i++)if(coherent[i].cpu==p&&!coherent[i].freed){assert(coherent[i].bytes==bytes&&coherent[i].dma==dma);coherent[i].freed=true;free(p);return;}assert(0);}
static void *kcalloc(unsigned n,unsigned bytes,int flags){(void)flags;assert(n==514);return fail_meta?NULL:calloc(n,bytes);}
static void kfree(void *p){free(p);}
'''
case=r'''
static void check(unsigned a,unsigned m,unsigned c,bool meta){
 memset(skbs,0,sizeof(skbs));memset(coherent,0,sizeof(coherent));nskb=ncoh=alloc_calls=map_calls=coh_calls=largest=0;fail_alloc=a;fail_map=m;fail_coh=c;fail_meta=meta;
 struct device dev={1};struct rtl_ax_rx rx={.pe_device=&dev};int ret=rtl_ax_rx_buffers_alloc(&rx);
 if(!a&&!m&&!c&&!meta){
  assert(!ret&&nskb==514&&ncoh==4&&largest==262144&&rx.queues[0].allocated_payloads==514);
  for(unsigned i=0;i<514;i++){
   struct rtl_ax_rx_buffer *b=&rx.queues[0].payloads[i];assert(b->data==b->skb->data&&read32(b->data)==0);
   assert(read32(rx.queues[0].addresses+i)==b->dma);
   if(i>=256)continue;
   assert(rx.queues[0].slot_ids[i]==i);
   assert(read32(rx.queues[0].descriptors+i*8)==12288&&read32(rx.queues[0].descriptors+i*8+4)==b->dma);
   assert(read32(rx.queues[1].descriptors+i*8)==1024&&read32(rx.queues[1].descriptors+i*8+4)==rx.queues[1].buffer_dma+i*1024);
  }
 }else assert(ret==(m?-EIO:-ENOMEM));
 rtl_ax_rx_buffers_free(&rx);
 for(unsigned i=0;i<nskb;i++)assert(skbs[i].freed&&!skbs[i].mapped);
 for(unsigned i=0;i<ncoh;i++)assert(coherent[i].freed);
}
int main(void){check(0,0,0,false);check(10,0,0,false);check(0,10,0,false);check(0,0,2,false);check(0,0,3,false);check(0,0,4,false);check(0,0,0,true);}
'''
with tempfile.TemporaryDirectory(prefix='rx-skb-alloc-',dir=root/'artifacts') as tmp:
 p=Path(tmp);c=p/'case.c';c.write_text(common+''.join(function(n) for n in ['rtl_ax_rx_buffer_alloc','rtl_ax_rx_buffers_alloc','rtl_ax_rx_buffers_free'])+case)
 subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(driver),str(c),'-o',str(p/'case')],check=True)
 subprocess.run([str(p/'case')],check=True)
print('514 mapped skbs, coherent address directory, 256 RXBD owners and partial allocation/map failures passed.')
