#!/usr/bin/env python3
"""Exercise the real NI poll with a 65-frame arrival during its first callback."""
from pathlib import Path
import json, subprocess, sys
root=Path(__file__).resolve().parents[1]
relative='port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c'
before='--before' in sys.argv
source=subprocess.check_output(['git','show','0a90b2739755574c5f5ed46950919dd4eb67a22a:'+relative],cwd=root,text=True) if before else (root/relative).read_text()
a=source.index('static void rtl_qm_rx_retire(') if 'static void rtl_qm_rx_retire(' in source else source.index('int rtl_qm_rx_poll(')
b=source.index('\nssize_t rtl_qm_rx_state(',a)
out=root/'artifacts/test-ni-rx-credit'/('before' if before else 'after');out.mkdir(parents=True,exist_ok=True)
common=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;typedef uintptr_t dma_addr_t;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define FIELD_GET(mask,x) (((x)&(mask))>>__builtin_ctz(mask))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define READ_ONCE(x) (x)
#define lower_32_bits(x) ((u32)(x))
#define dma_rmb() ((void)0)
#define mb() ((void)0)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define rcu_dereference(x) (x)
#define ETH_HLEN 14
#define ETH_P_IP 0x0800
#define IPPROTO_UDP 17
#define CHECKSUM_UNNECESSARY 1
#define DMA_FROM_DEVICE 2
#define NAPI_POLL_WEIGHT 64
#define RTL_QM_RING_BYTES 1024
#define RTL_QM_COMMAND_BYTES 8
#define RTL_QM_VOQS 8
#include "rtl9617c-qm-layout.h"
#define RTL_QM_MEMORY_BYTES (8192+512*2048)
#define EQ_ID 4
#define EPP_WRITE 0x7000
#define EPP_READ 0x7100
#include "rtl9617c-qm-refill.h"
#include "rtl9617c-wifi-format.h"
struct net_device {bool running;struct {u64 rx_errors,rx_dropped,rx_packets,rx_bytes;} stats;};
struct napi_struct {struct net_device *dev;};
struct sk_buff {u8 storage[128],*data;unsigned len,priority,protocol,ip_summed;struct net_device *dev;};
struct rtl9617c_ni_wifi_endpoint {struct net_device *netdev;void (*receive)(struct rtl9617c_ni_wifi_endpoint *,struct sk_buff *,bool);};
struct rtl9617c_ni_endpoint {void (*receive)(struct rtl9617c_ni_endpoint *,struct napi_struct *,struct sk_buff *,unsigned);};
struct rtl_qm_rx {
 void *ni,*ring_memory,*memory,*device;dma_addr_t memory_dma;unsigned read[8];
 u32 last_address,last_size,last_header[2],trace[32][7];unsigned trace_count;
 struct rtl_qm_refill refill;struct rtl9617c_ni_wifi_endpoint *wifi[61];struct rtl9617c_ni_endpoint *pon;struct net_device *lan[5];bool profile;
 u64 host_poll_ns,host_polls,wifi_copy_ns,wifi_forwarded,wifi_missed,wifi_copy_bytes,wifi_dispatch_ns;
};
static struct rtl_qm_rx *active;
static unsigned producer,consumer,drops,kind,delivered,injected;
static bool seen[129];
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static u32 get_unaligned_be32(const void *p){return __builtin_bswap32(get_unaligned_le32(p));}
static u16 get_unaligned_be16(const void *p){u16 v;memcpy(&v,p,2);return __builtin_bswap16(v);}
static void put(u32 v,void *p){memcpy(p,&v,4);}
static u64 ktime_get_ns(void){static u64 t;return ++t;}
static u32 readl(void *p){unsigned r=(u8 *)p-(u8 *)active->ni;
 if(r==0x3218)return 0;if(r==0x659c)return producer!=consumer;
 if(r>=EPP_WRITE&&r<EPP_WRITE+32)return r==EPP_WRITE?(producer%128)*8:0;
 assert(false);return 0;}
static void writel(u32 v,void *p){unsigned r=(u8 *)p-(u8 *)active->ni;
 if(r==EPP_READ){consumer+=((v-(consumer%128)*8)&1023)/8;assert(consumer<=producer);}
 else assert(r>EPP_READ&&r<EPP_READ+32&&v==0);
}
static void dma_sync_single_for_cpu(void *d,dma_addr_t a,unsigned n,int direction){(void)d;(void)a;assert(n==2048&&direction==2);}
static void dma_sync_single_for_device(void *d,dma_addr_t a,unsigned n,int direction){dma_sync_single_for_cpu(d,a,n,direction);}
static int rtl_qm_try_push(void *context,unsigned address,unsigned eq){struct rtl_qm_rx *rx=context;
 unsigned id=(address-(u32)rx->memory_dma-8192)/2048;
 assert(eq==4&&consumer>id);return 0;}
static bool rtl_rx_checksum_valid(u32 c,const void *p,unsigned n){(void)c;(void)p;(void)n;return false;}
static struct sk_buff *napi_alloc_skb(struct napi_struct *n,unsigned length){(void)n;assert(length==64);struct sk_buff *s=calloc(1,sizeof(*s));assert(s);s->data=s->storage;return s;}
static void *skb_put(struct sk_buff *s,unsigned n){void *p=s->data+s->len;s->len+=n;return p;}
static void dev_kfree_skb_any(struct sk_buff *s){free(s);}
static bool netif_running(struct net_device *d){return d->running;}
static unsigned eth_type_trans(struct sk_buff *s,struct net_device *d){(void)d;s->data+=14;s->len-=14;return ETH_P_IP;}
static void packet_bytes(unsigned id,u8 *b){memset(b,0xa5,64);b[0]=2;b[12]=8;b[13]=0;b[14]=0x45;b[23]=17;b[36]=18081>>8;b[37]=18081&255;put(__builtin_bswap32(id),b+42);}
static void enqueue(unsigned id){
 if(producer-consumer==128){drops++;return;}
 unsigned offset=8192+id*2048,header=kind==1?16:8;u32 h0=kind==2?5U<<18:0,h1=64U<<15|(kind==2?7U<<9:0);
 put(__builtin_bswap32(h0),active->memory+offset+RTL_QM_HEADROOM);put(__builtin_bswap32(h1),active->memory+offset+RTL_QM_HEADROOM+4);
 packet_bytes(id,active->memory+offset+RTL_QM_HEADROOM+header);
 void *entry=active->ring_memory+(producer%128)*8;
 put(((u32)active->memory_dma+offset)|0x54,entry);put(kind==1?1U<<14|80:0,entry+4);producer++;
}
static void received(struct sk_buff *s){unsigned skip=kind==0?14:0;u8 expected[64];unsigned id=get_unaligned_be32(s->data+42-skip);
 assert(id<129&&!seen[id]);seen[id]=true;packet_bytes(id,expected);assert(s->len==64-skip&&!memcmp(s->data,expected+skip,s->len));delivered++;
 if(!injected++){for(unsigned i=64;i<129;i++)enqueue(i);}free(s);
}
static void napi_gro_receive(struct napi_struct *n,struct sk_buff *s){(void)n;assert(kind==0);received(s);}
static void wifi_receive(struct rtl9617c_ni_wifi_endpoint *e,struct sk_buff *s,bool f){(void)e;assert(kind==1&&f);received(s);}
static void pon_receive(struct rtl9617c_ni_endpoint *e,struct napi_struct *n,struct sk_buff *s,unsigned metadata){(void)e;(void)n;assert(kind==2&&metadata==(7|5<<8));received(s);}
'''
case=r'''
int main(void){
 for(unsigned mode=0;mode<3;mode++){
  kind=(mode+1)%3;producer=consumer=drops=delivered=injected=0;memset(seen,0,sizeof(seen));
  struct net_device owner={.running=true},input={.running=true};struct napi_struct napi={.dev=&owner};
  struct rtl9617c_ni_wifi_endpoint wifi={.netdev=&input,.receive=wifi_receive};struct rtl9617c_ni_endpoint pon={.receive=pon_receive};
  struct rtl_qm_rx rx={.memory_dma=0x04000000,.device=&owner,.pon=&pon};rx.wifi[1]=&wifi;rx.lan[0]=&input;
  rx.memory=calloc(1,RTL_QM_MEMORY_BYTES);rx.ring_memory=rx.memory;rx.ni=calloc(1,0x10000);active=&rx;
  for(unsigned i=0;i<64;i++)enqueue(i);
  for(unsigned turns=0;producer!=consumer&&turns<4;turns++)assert(rtl_qm_rx_poll(&rx,&napi,64)>0);
  printf("kind=%u accepted=%u received=%u drops=%u returned=%u\n",kind,producer,delivered,drops,rx.refill.returned);fflush(stdout);
  assert(!drops&&delivered==129&&rx.refill.returned==129&&!rx.refill.pending);
  for(unsigned i=0;i<129;i++)assert(seen[i]);
  if(kind==0){assert(input.stats.rx_packets==129&&rx.trace_count==32);for(unsigned i=0;i<32;i++)assert(rx.trace[i][0]==i*8);}
  free(rx.ni);free(rx.memory);
 }
 kind=1;producer=consumer=drops=delivered=injected=0;
 struct net_device owner={.running=true};struct napi_struct napi={.dev=&owner};
 struct rtl_qm_rx rx={.memory_dma=0x04000000,.device=&owner};
 rx.memory=calloc(1,RTL_QM_MEMORY_BYTES);rx.ring_memory=rx.memory;rx.ni=calloc(1,0x10000);active=&rx;
 enqueue(0);put(1U<<14|8,rx.ring_memory+4);
 assert(rtl_qm_rx_poll(&rx,&napi,64)==1&&consumer==1&&delivered==0);
 assert(owner.stats.rx_errors==1&&rx.refill.returned==1);
 free(rx.ni);free(rx.memory);
 for(unsigned full=0;full<2;full++) {
  kind=1;producer=consumer=drops=delivered=injected=0;
  struct rtl_qm_rx edge={.memory_dma=0x04000000,.device=&owner};
  edge.memory=calloc(1,RTL_QM_MEMORY_BYTES);edge.ring_memory=edge.memory;edge.ni=calloc(1,0x10000);active=&edge;
  unsigned errors=owner.stats.rx_errors,header=full?16:8;
  enqueue(0);
  put(0,edge.ring_memory+4);
  put(__builtin_bswap32(full?BIT(31):0),edge.memory+8192+RTL_QM_HEADROOM);
  put(__builtin_bswap32((RTL_QM_DATA_BYTES-header+1+(full?8:0))<<15),edge.memory+8192+RTL_QM_HEADROOM+4);
  assert(rtl_qm_rx_poll(&edge,&napi,64)==1 && owner.stats.rx_errors==errors+1);
  assert(edge.refill.returned==1 && delivered==0);
  free(edge.ni);free(edge.memory);
 }
}
'''
(out/'credit.c').write_text(common+source[a:b]+case)
subprocess.run(['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=address,undefined','-I',str(root/relative).rsplit('/',1)[0],'-I',str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),str(out/'credit.c'),'-o',str(out/'credit')],check=True)
result=subprocess.run([str(out/'credit')],capture_output=True,text=True)
print(result.stdout,end='')
if before:
 assert result.returncode!=0 and 'drops=1' in result.stdout
 print('Original poll reproduces one dropped frame while its first callback holds EPP credit.')
else:
 if result.returncode:print(result.stderr,file=sys.stderr);raise SystemExit(result.returncode)
 (out/'result.json').write_text(json.dumps({'actual_poll_C':True,'EPP_slots':128,'initial_frames':64,'arrival_during_callback':65,'drops':0,'WiFi_LAN_PON_payloads_exact':True,'BID_return_follows_EPP_publication':True,'LAN_trace_cursor_preserved':True,'scope':'Actual NI poll and refill code; DMA and callback-time arrivals modeled. No throughput claim.'},indent=2)+'\n')
