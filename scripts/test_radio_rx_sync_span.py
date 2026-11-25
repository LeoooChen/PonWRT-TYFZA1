#!/usr/bin/env python3
"""Check ownership and validated DMA ranges in the actual PE delivery function."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[1]
s=(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rx.c').read_text()
a=s.index('static void rtl_ax_rx_pe_deliver(');b=s.index('static void rtl_ax_rx_pe_rxq_drain(',a)
code=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uintptr_t dma_addr_t;
#define BIT(n) (1U<<(n))
#define RTL_AX_RX_WORK_IDS 0x1a000
#define RTL_AX_RX_WORK_STATUS 0xf000
#define RTL_AX_RX_WORK_OLD BIT(31)
#define DMA_BIDIRECTIONAL 0
#define ALIGN(n,a) (((n)+(a)-1)&~((a)-1))
struct rtl_ax_rx_packet {int unused;};
struct rtl_ax_rx_buffer {u8 *data;dma_addr_t dma;};
struct rtl_ax_rx_queue {unsigned buffer_bytes;struct rtl_ax_rx_buffer *payloads;};
struct rtl_ax_rx {struct {u8*memory;}pe;struct rtl_ax_rx_queue queues[1];void *pe_device;unsigned pe_rx_delivered,pe_rx_malformed;};
static u8 memory[0x20000],cached[12288],device_bytes[12288];
static unsigned acquired,released,dispatches,seeds;static bool fenced;static unsigned line=128;
static u32 get_unaligned_le32(const void*p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void*p){assert(fenced&&released==acquired);memcpy(p,&v,4);}
static unsigned dma_get_cache_alignment(void){return line;}
static void dma_sync_single_for_cpu(void*d,dma_addr_t a,unsigned n,int dir){(void)d;(void)dir;assert(a==(uintptr_t)device_bytes&&n==line);memcpy(cached,device_bytes,n);acquired=n;}
static void dma_sync_single_range_for_cpu(void*d,dma_addr_t a,unsigned off,unsigned n,int dir){(void)d;(void)dir;assert(a==(uintptr_t)device_bytes&&off==acquired&&n&&n%line==0&&off+n<=12288);memcpy(cached+off,device_bytes+off,n);acquired+=n;}
static void dma_sync_single_for_device(void*d,dma_addr_t a,unsigned n,int dir){(void)d;(void)dir;assert(a==(uintptr_t)device_bytes&&n==acquired);released=n;}
static void dma_wmb(void){fenced=true;}
static void rtl_ax_rx_uplink_sync(struct rtl_ax_rx*r){(void)r;}
static int rtl_ax_receive_packet(u8*b,unsigned cap,struct rtl_ax_rx_packet*p){(void)p;unsigned n=get_unaligned_le32(b)&0x3fff;assert(n==cap&&acquired>=n&&!memcmp(b,device_bytes,n));return (get_unaligned_le32(b)&0xc000)==0xc000?0:-EINVAL;}
static void rtl_ax_rx_dispatch(struct rtl_ax_rx*r,bool old,struct rtl_ax_rx_packet*p){(void)r;(void)p;assert(old);dispatches++;}
static void rtl_ax_rx_uplink_seed(struct rtl_ax_rx*r,struct rtl_ax_rx_packet*p){(void)r;(void)p;seeds++;}
'''+s[a:b]+r'''
int main(void){
 struct rtl_ax_rx_buffer payload={cached,(uintptr_t)device_bytes};struct rtl_ax_rx rx={.pe={memory},.queues={{12288,&payload}}};
 unsigned sizes[]={0,19,20,32,63,64,65,127,128,129,1538,11490,12288,12289,16383};
 for(line=64;line<=128;line*=2)for(unsigned i=0;i<sizeof(sizes)/sizeof(sizes[0]);i++)for(unsigned fs=0;fs<2;fs++){
  memset(cached,0xa5,sizeof(cached));memset(device_bytes,0x79,sizeof(device_bytes));u32 info=sizes[i]|(fs?0xc000:0);memcpy(device_bytes,&info,4);
  u32 item=RTL_AX_RX_WORK_OLD;memcpy(memory+RTL_AX_RX_WORK_IDS,&item,4);
  acquired=released=dispatches=seeds=0;fenced=false;
  unsigned delivered=rx.pe_rx_delivered,malformed=rx.pe_rx_malformed;
  rtl_ax_rx_pe_deliver(&rx,0);
  bool valid=sizes[i]>=20&&sizes[i]<=12288;
  assert(acquired==(valid?ALIGN(sizes[i],line):line));
  assert(dispatches==(valid&&fs)&&seeds==dispatches);
  assert(rx.pe_rx_delivered-delivered==dispatches&&rx.pe_rx_malformed-malformed==!dispatches);
  assert(get_unaligned_le32(memory+RTL_AX_RX_WORK_STATUS)==BIT(30));
 }
 puts("PE RX sync ranges: header-first, boundary lengths, malformed frames and release ordering PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='rx-sync-span-') as d:
 d=Path(d);(d/'test.c').write_text(code)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(d/'test.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True)
