#!/usr/bin/env python3
"""Run the iperf TCP/UDP feed callbacks against modeled NI consumption."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'tools/iperf-pon/iperf_pon_feed.c').read_text()


def function(name):
    at = source.index(name + '(')
    start = source.rfind('\n', 0, at) + 1
    end = source.index('{', at) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


prefix = r'''
#include <arpa/inet.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
typedef int netdev_tx_t;
#define ETH_P_IP 0x800
#define ETH_P_PPP_SES 0x8864
#define ETH_ALEN 6
#define ETH_HLEN 14
#define CHECKSUM_PARTIAL 1
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define HRTIMER_MODE_REL 0
#define READ_ONCE(x) (x)
#define min(a,b) ((a)<(b)?(a):(b))
enum hrtimer_restart {HRTIMER_NORESTART,HRTIMER_RESTART};
struct hrtimer {int armed;};
struct net_device {u8 dev_addr[6];bool stopped;struct {u64 tx_packets,tx_bytes;} stats;};
struct sk_buff {u16 protocol;unsigned len;u8 *data;struct net_device *dev;bool consumed,gso;int ip_summed;};
struct iphdr {int protocol;};
struct tcphdr {u16 source,dest;bool syn,fin,rst;};
struct udphdr {u16 source,dest;};
static struct iphdr ip={.protocol=6};
static struct tcphdr tcp;
static struct udphdr udp_header;
static struct net_device devices[3],*feed=&devices[0],*radio=&devices[1],*bridge=&devices[2];
static struct hrtimer timer;
static u32 registers[256],producer;
static void *vp=registers,*memory;
static u32 eqregs[64];static void *eq_monitor;static unsigned output_eq;
static u64 data_packets,control_packets,data_bytes,stops,wakes,doorbells;
static u64 data_prepare_ns,payload_copy_ns,stream_packets[4];
static u64 invalid_packets;
static bool udp;
static bool pending;
static unsigned control_transmits;
static struct iphdr *ip_hdr(struct sk_buff *s){return &ip;}
static struct tcphdr *tcp_hdr(struct sk_buff *s){return &tcp;}
static struct udphdr *udp_hdr(struct sk_buff *s){return &udp_header;}
static bool skb_is_gso(struct sk_buff *s){return s->gso;}
static int skb_checksum_help(struct sk_buff *s){s->ip_summed=0;return 0;}
static void dev_kfree_skb_any(struct sk_buff *s){s->consumed=true;}
static u32 readl(const void *p){u32 x;memcpy(&x,p,4);return x;}
static void writel(u32 x,void *p){memcpy(p,&x,4);}
static u64 ktime_get_ns(void){static u64 x;return ++x;}
static u64 ns_to_ktime(u64 x){return x;}
static void hrtimer_start(struct hrtimer *t,u64 n,int mode){t->armed=1;}
static void hrtimer_forward_now(struct hrtimer *t,u64 n){}
static void netif_stop_queue(struct net_device *d){d->stopped=true;}
static void netif_wake_queue(struct net_device *d){d->stopped=false;}
static bool netdev_xmit_more(void){return true;}
static void dma_wmb(void){}
static void skb_copy_bits(struct sk_buff *s,unsigned at,void *out,unsigned n){assert(at+n<=s->len);memcpy(out,s->data+at,n);}
static void skb_set_queue_mapping(struct sk_buff *s,unsigned n){assert(n==0);}
static void dev_queue_xmit(struct sk_buff *s){assert(s->dev==radio);assert(readl(vp+0x18)==producer);control_transmits++;}
static void dev_consume_skb_any(struct sk_buff *s){s->consumed=true;}
static void put_unaligned_be16(u16 x,void *p){x=htons(x);memcpy(p,&x,2);}
static void put_unaligned_le32(u32 x,void *p){u8 *b=p;for(unsigned i=0;i<4;i++)b[i]=x>>(8*i);}
'''

checks = r'''
int main(void){
 u8 packet[1506],held[2][1536];unsigned held_slot[2]={0};bool have_held=false;
 memory=calloc(1024,1536+8);assert(memory);
 memcpy(bridge->dev_addr,"\x98\x1e\x89\x61\x68\xd0",6);
 tcp.source=htons(5206);tcp.dest=htons(55001);
 producer=1022;writel(producer,vp+0x1c);
 for(unsigned batch=0;batch<40;batch++){
  u64 prior=doorbells;
  for(unsigned i=0;i<61;i++){
   unsigned slot=producer;
   for(unsigned j=0;j<sizeof(packet);j++)packet[j]=(batch*61+i+j)&255;
   struct sk_buff skb={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet};
   assert(transmit(&skb,feed)==NETDEV_TX_OK && skb.consumed);
   u8 *frame=memory+8192+slot*1536;
   assert(!memcmp(frame,bridge->dev_addr,6));
   assert(!memcmp(frame+6,"\x02\x55\x46\x50\x4f\x4e\x88\x64\x11\x00\x12\x34\x05\xd6\x00\x21",16));
   assert(!memcmp(frame+22,packet+14,sizeof(packet)-14));
   assert(readl(memory+slot*8+4)==(0x81000001U|(7U<<25)|(1514U<<8)));
   assert(producer==((slot+1)&1023));
  }
  assert(!room() && doorbells==prior+1 && readl(vp+0x18)==producer);
  struct sk_buff blocked={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet};
  assert(transmit(&blocked,feed)==NETDEV_TX_BUSY && !blocked.consumed);
  assert(feed->stopped && timer.armed);
  assert(tick(&timer)==HRTIMER_RESTART);
  if(have_held)for(unsigned i=0;i<2;i++)
   assert(!memcmp(held[i],memory+8192+held_slot[i]*1536,1536));
  for(unsigned i=0;i<2;i++){
   held_slot[i]=(producer-i-1)&1023;
   memcpy(held[i],memory+8192+held_slot[i]*1536,1536);
  }
  have_held=true;writel(producer,vp+0x1c);
  assert(tick(&timer)==HRTIMER_NORESTART && !feed->stopped && room()==61);
 }
 assert(data_packets==2440 && stream_packets[1]==2440);
 tcp.syn=true;
 struct sk_buff syn={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet};
 assert(transmit(&syn,feed)==NETDEV_TX_OK && !syn.consumed);
 assert(control_packets==1 && control_transmits==1 && data_packets==2440);
 // A control skb can finish a GSO batch while its last data skb has xmit_more.
 tcp.syn=false;
 for(unsigned flag=0;flag<3;flag++){
  u64 prior=doorbells;
  struct sk_buff data={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet};
  assert(transmit(&data,feed)==NETDEV_TX_OK && data.consumed);
  assert(doorbells==prior && readl(vp+0x18)!=producer);
  tcp.syn=flag==0;tcp.fin=flag==1;tcp.rst=flag==2;
  struct sk_buff control={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet};
  assert(transmit(&control,feed)==NETDEV_TX_OK && !control.consumed);
  assert(doorbells==prior+1 && readl(vp+0x18)==producer);
  assert(transmit(&control,feed)==NETDEV_TX_OK && doorbells==prior+1);
  tcp.syn=tcp.fin=tcp.rst=false;
  writel(producer,vp+0x1c);
 }
 udp=true;ip.protocol=17;udp_header.source=htons(5206);udp_header.dest=htons(55002);
 struct sk_buff datagram={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet,.ip_summed=CHECKSUM_PARTIAL};
 u64 before=stream_packets[2];
 assert(transmit(&datagram,feed)==NETDEV_TX_OK && datagram.consumed);
 assert(datagram.ip_summed==0 && stream_packets[2]==before+1);
 struct sk_buff oversized={.protocol=htons(ETH_P_IP),.len=1507,.data=packet};
 assert(transmit(&oversized,feed)==NETDEV_TX_OK && oversized.consumed && invalid_packets==1);
 struct sk_buff gso={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet,.gso=true};
 assert(transmit(&gso,feed)==NETDEV_TX_OK && gso.consumed && invalid_packets==2);
 udp_header.source=htons(1234);
 struct sk_buff other={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet};
 assert(transmit(&other,feed)==NETDEV_TX_OK && !other.consumed);
 // Optional EQ backpressure limits new arrivals and flushes deferred DMA.
 tcp.syn=tcp.fin=tcp.rst=false;udp=false;ip.protocol=6;
 for(unsigned eq=6;eq<=7;eq++){
  output_eq=eq;eq_monitor=eqregs;writel(producer,vp+0x1c);
  writel(130,eq_monitor+eq*16);
  assert(room()==2);
  u64 before=doorbells;
  struct sk_buff data={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet};
  assert(transmit(&data,feed)==NETDEV_TX_OK && data.consumed && pending);
  writel(128,eq_monitor+eq*16);
  struct sk_buff blocked={.protocol=htons(ETH_P_IP),.len=sizeof(packet),.data=packet};
  assert(transmit(&blocked,feed)==NETDEV_TX_BUSY && !blocked.consumed);
  assert(!pending && doorbells==before+1 && readl(vp+0x18)==producer);
  assert(tick(&timer)==HRTIMER_RESTART);
  writel(producer,vp+0x1c);writel(129,eq_monitor+eq*16);
  assert(tick(&timer)==HRTIMER_NORESTART && room()==1);
 }
 eq_monitor=NULL;assert(room()==61);
 free(memory);
}
'''

with tempfile.TemporaryDirectory(prefix='pon-tcp-feed-') as directory:
    path = Path(directory)
    program = prefix + '\n'.join(function(n) for n in ('room', 'tick', 'transmit')) + checks
    (path / 'test.c').write_text(program)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
print('iperf feed: TCP/UDP selection, checksum, bounds, ring wrap, backpressure and control publication passed')
