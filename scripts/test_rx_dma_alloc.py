#!/usr/bin/env python3
"""Execute production DMA pool allocation/free with fragmented buffer addresses."""
from pathlib import Path
import json,re,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=(D/'rx.c').read_text()
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
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint32_t __le32;typedef uint32_t dma_addr_t;
#include "receive.h"
#define GFP_KERNEL 0
#define lower_32_bits(v) (v)
#define cpu_to_le32(v) (v)
struct device {unsigned id;};
struct rtl_ax_rx_buffer {u8 *data;dma_addr_t dma;};
struct rtl_ax_rx_queue {u8 *descriptors,*buffers;dma_addr_t descriptor_dma,buffer_dma;
 struct rtl_ax_rx_buffer *payloads;__le32 *addresses;dma_addr_t addresses_dma;unsigned allocated_payloads;
 u16 buffer_ids[RTL_AX_RX_ENTRIES];unsigned buffer_bytes;};
struct rtl_ax_rx {struct device *pe_device;struct rtl_ax_rx_queue queues[2];};
struct allocation {void *pointer;dma_addr_t dma;size_t bytes;bool freed;};
static struct allocation allocations[520];
static unsigned calls,count,fail_at,payload_ids,other_ids,freed;static size_t largest;static bool metadata_fail;
static void *dma_alloc_coherent(struct device *dev,size_t bytes,dma_addr_t *dma,int flags){
 (void)flags;assert(dev->id==1);calls++;if(calls==fail_at || bytes>4*1024*1024)return NULL;
 if(bytes>largest)largest=bytes;
 if(bytes==12288)*dma=0x08000000+((payload_ids++*73)%514)*65536;
 else *dma=0x01000000+(other_ids++)*1048576;
 void *p=malloc(bytes);assert(p);memset(p,0xa5,bytes);allocations[count++]=(struct allocation){p,*dma,bytes,false};return p;
}
static void dma_free_coherent(struct device *dev,size_t bytes,void *p,dma_addr_t dma){
 assert(dev->id==1);
 for(unsigned i=0;i<count;i++)if(allocations[i].pointer==p && !allocations[i].freed){
  assert(allocations[i].dma==dma && allocations[i].bytes==bytes);allocations[i].freed=true;freed++;free(p);return;
 }
 assert(!"unknown or already freed DMA buffer");
}
static void *kcalloc(size_t n,size_t bytes,int flags){(void)flags;return metadata_fail?NULL:calloc(n,bytes);}
static void kfree(void *p){free(p);}
static u32 read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
'''
case=r'''
static void check(unsigned failure,bool meta){
 memset(allocations,0,sizeof(allocations));calls=count=freed=payload_ids=other_ids=0;largest=0;fail_at=failure;metadata_fail=meta;
 struct device device={1};struct rtl_ax_rx rx={.pe_device=&device};
 int ret=rtl_ax_rx_buffers_alloc(&rx);
 if(!failure && !meta){
  assert(!ret && count==518 && rx.queues[0].allocated_payloads==514 && largest==262144);
  assert(rx.queues[0].addresses_dma!=rx.queues[0].payloads[0].dma);
  for(unsigned id=0;id<514;id++){
   assert(rx.queues[0].addresses[id]==rx.queues[0].payloads[id].dma);
   for(unsigned j=0;j<12288;j++)assert(rx.queues[0].payloads[id].data[j]==0);
  }
  for(unsigned i=0;i<256;i++){
   assert(rx.queues[0].buffer_ids[i]==i);
   assert(read32(rx.queues[0].descriptors+i*8)==12288);
   assert(read32(rx.queues[0].descriptors+i*8+4)==rx.queues[0].payloads[i].dma);
   assert(read32(rx.queues[1].descriptors+i*8+4)==rx.queues[1].buffer_dma+i*1024);
  }
 }else assert(ret==-ENOMEM);
 rtl_ax_rx_buffers_free(&rx);assert(freed==count);
}
int main(void){
 assert(RTL_AX_RX_BUFFERS*RTL_AX_RXQ_BUFFER_BYTES>4*1024*1024);
 check(0,false);
 unsigned failures[]={1,2,16,516,517,518};
 for(unsigned i=0;i<sizeof(failures)/sizeof(failures[0]);i++)check(failures[i],false);
 check(0,true);
}
'''
with tempfile.TemporaryDirectory(prefix='rx-dma-alloc-',dir=ROOT/'artifacts') as temp:
 p=Path(temp);c=p/'case.c';c.write_text(common+function('rtl_ax_rx_buffers_alloc')+function('rtl_ax_rx_buffers_free')+case)
 subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(D),str(c),'-o',str(p/'case')],check=True)
 subprocess.run([str(p/'case')],check=True)
print(json.dumps({'scope':'Actual production allocation/free functions with a4MiB limit and permuted per-payload DMA addresses; no physical allocator or hardware execution.',
 'individual_RXQ_payload_allocations':514,'payload_allocation_bytes':12288,'largest_DMA_allocation_bytes':262144,
 'RXBD_and_PE_address_table_match':True,'partial_failure_cases':7,'allocated_buffers_freed_exactly_once':True,
 'sanitizers':['ASan','UBSan'],'no_large_contiguous_pool_or_fallback':True},indent=2))
