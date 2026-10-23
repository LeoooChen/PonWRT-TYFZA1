#!/usr/bin/env python3
"""Exercise production QM allocation and BID publication with separated DMA chunks."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else (drivers / 'rtl9617c-qm.c').read_text()
start = source.find('static unsigned int rtl_qm_wifi_chunk_bytes(')
if start < 0:
    start = source.index('int rtl_qm_rx_alloc(')
allocation = source[start:source.index('\nstatic unsigned int rtl_dq_read', start)]
start = source.index('void rtl_qm_rx_free(')
end = source.index('\nstatic void rtl_qm_rx_retire(', start) if 'static void rtl_qm_rx_retire(' in source else source.index('\nint rtl_qm_rx_poll(', start)
release = source[start:end]
start = source.index('\tfor (unsigned int pool = 0;', source.index('int rtl_qm_rx_start('))
publication = source[start:source.index('\tret = rtl_qm_cpu_credits', start)]
fixture = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#define RTL_QM_PE_POOLS 2
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31-(h))))
#define RTL_QM_PACKET_LAYOUT 0x18041804
#define RTL_QM_PE_BUFFERS 2496
#define RTL_QM_PE_CHUNK_BUFFERS 1024
#define RTL_QM_PE_CHUNKS 3
#define RTL9617C_WIFI_EPP_BYTES 32768
#define RTL_QM_PE_EQ 6
#define RTL_QM_BUFFER_BYTES 2048
#define RTL_DQ_MEMORY_BYTES (1024*2048)
#define RTL_QM_RING_BYTES 1024
#define RTL_QM_VOQS 8
#define RTL_QM_MEMORY_BYTES (RTL_QM_RING_BYTES*RTL_QM_VOQS+512*2048)
#define RTL_QM_DRAM_BYTES (RTL_DQ_MEMORY_BYTES+2*2496*2048)
#define GFP_KERNEL 0
#define DMA_FROM_DEVICE 0
#define min_t(type,a,b) ((type)(a)<(type)(b)?(type)(a):(type)(b))
typedef uint64_t dma_addr_t;
typedef uint32_t u32;
#define EPP_BASE 0x7200
#define EPP_WRITE 0x7000
#define EPP_READ 0x7100
static unsigned char ni[65536];
static void writel(u32 value,void *p){memcpy(p,&value,4);}
static u32 readl(const void *p){u32 value;memcpy(&value,p,4);return value;}
struct device { int unused; };
struct rtl_qm_rx {
 struct device *device;
 void *memory,*ring_memory,*deepq_memory;
 dma_addr_t memory_dma,ring_dma,deepq_dma;
 void *wifi_memory[2][3];dma_addr_t wifi_dma[2][3];
 void *wifi_epp[2];dma_addr_t wifi_epp_dma[2];
};
static struct {void *memory;dma_addr_t dma;unsigned int bytes;} records[16];
static unsigned int calls,live,reject,published;
static unsigned char seen[2][2496];
static void *dma_alloc_coherent(struct device *dev,unsigned int bytes,dma_addr_t *dma,int flags)
{
 (void)dev;(void)flags;calls++;
 if(calls==reject || bytes>4*1024*1024)return NULL;
 assert(calls<16);void *p=calloc(1,bytes);assert(p);
 *dma=0x04000000ULL+calls*0x01000000ULL;
 records[calls]=(typeof(records[0])){p,*dma,bytes};live++;return p;
}
static void dma_free_coherent(struct device *dev,unsigned int bytes,void *memory,dma_addr_t dma)
{
 (void)dev;
 for(unsigned int i=1;i<16;i++)if(records[i].memory==memory) {
  assert(memory && records[i].dma==dma && records[i].bytes==bytes);
  free(memory);records[i].memory=NULL;live--;return;
 }
 assert(0);
}
#define dma_alloc_noncoherent(d,b,a,dir,f) dma_alloc_coherent(d,b,a,f)
#define dma_free_noncoherent(d,b,p,a,dir) dma_free_coherent(d,b,p,a)
static int rtl_qm_push(struct rtl_qm_rx *rx,dma_addr_t address,unsigned int eq)
{
 unsigned int pool=eq-6;
 assert(pool<2);
 for(unsigned int c=0;c<3;c++)if(address>=rx->wifi_dma[pool][c] &&
    address<rx->wifi_dma[pool][c]+(c==2?448:1024)*2048) {
  unsigned int offset=address-rx->wifi_dma[pool][c];assert(!(offset%2048));
  unsigned int id=c*1024+offset/2048;assert(id<2492 && !seen[pool][id]);
  seen[pool][id]=1;published++;return 0;
 }
 assert(0);return -1;
}
void rtl_qm_rx_free(struct device *,struct rtl_qm_rx *);
'''
test = r'''
int main(void) {
 struct device dev={};struct rtl_qm_rx rx={};
 assert(!rtl_qm_rx_alloc(&dev,&rx));assert(live==11);
 assert(!publish(&rx) && published==2*2492);
 for(unsigned int p=0;p<2;p++) {
  assert(2492 < RTL9617C_WIFI_EPP_BYTES/8);
  assert(readl(ni+0x657c+(7-p)*4)==((RTL9617C_WIFI_EPP_BYTES/16)<<8|0xf2));
  for(unsigned int c=0;c<8;c++)
   assert(readl(ni+EPP_BASE+(3-p)*32+c*4)==rx.wifi_epp_dma[p]+c*RTL9617C_WIFI_EPP_BYTES);
 }
 for(unsigned int p=0;p<2;p++)for(unsigned int i=0;i<2492;i++)assert(seen[p][i]);
 rtl_qm_rx_free(&dev,&rx);assert(!live);
 for(reject=1;reject<=11;reject++) {
  calls=0;memset(&rx,0,sizeof(rx));memset(records,0,sizeof(records));
  assert(rtl_qm_rx_alloc(&dev,&rx)==-ENOMEM && !live && !rx.memory);
 }
 puts("QM: all2492 buffers per radio published across separate DMA chunks; 4MiB allocation limit and partial-allocation cleanup passed");
}
'''
with tempfile.TemporaryDirectory(prefix='qm-wifi-') as temp:
    work = Path(temp)
    (work / 'test.c').write_text(fixture + allocation + release +
        'static int publish(struct rtl_qm_rx *rx) { unsigned int i; int ret;\n' + publication + 'return 0;}\n' + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
