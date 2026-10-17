#!/usr/bin/env python3
"""Run production PON TX/RX, VLAN helpers and DMA on PPPoE Ethernet bytes.

SKB storage, MMIO and NAPI are host models; this is not an optical-line test.
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'


def function(file, name):
    source = (drivers / file).read_text()
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <arpa/inet.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/udp.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef int netdev_tx_t;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h)))&(~0U<<(l)))
#define READ_ONCE(x) (x)
#define ETH_HLEN 14
#define ETH_ALEN 6
#define VLAN_HLEN 4
#define VLAN_PRIO_SHIFT 13
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define ETH_P_PPP_SES 0x8864
#define ETH_P_IP 0x0800
#define ETH_P_IPV6 0x86dd
#define PPP_IP 0x21
#define PPP_IPV6 0x57
#define IP_OFFSET 0x1fff
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define GFP_ATOMIC 0
#define SKB_EXT_RTL9617C_BRIDGE 0
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define rcu_dereference(x) (x)
#define __iomem
#define dma_wmb() ((void)0)
#define max(a,b) ((a)>(b)?(a):(b))
#define cpu_to_le32(x) (x)
#include "rtl9617c-pon-path.h"
struct sk_buff {u8 memory[4096],*data;unsigned len,priority,protocol,vlan_proto,vlan_tci;bool tagged;};
struct net_device {bool carrier,running,stopped,bridge_port;struct net_device *upper;unsigned vlan_proto,vlan_id;struct {unsigned tx_dropped,rx_dropped,rx_packets,rx_bytes;}stats;};
static bool netif_is_bridge_port(struct net_device*d){return d->bridge_port;}
static struct net_device *__vlan_find_dev_deep_rcu(struct net_device*d,unsigned proto,unsigned vid){
 return d->upper && d->upper->vlan_proto==proto && d->upper->vlan_id==vid?d->upper:NULL;
}
struct napi_struct {unsigned unused;};
struct rtl9617c_ni_frame {u32 header_high,header_low;const u8 *prefix;unsigned prefix_length;bool raw_payload;};
struct rtl9617c_bridge_tag {u16 tci,tpid;};
struct rtl9617c_bridge_frame {unsigned pon_gem;unsigned long long pon_generation;unsigned tags;struct rtl9617c_bridge_tag tag[2];};
struct rtl_itu_path_state {struct rtl_itu_paths paths;};
struct rtl9617c_pon {struct rtl_itu_path_state *itu_paths;bool itu_paths_ready;void *ni_dev;struct net_device *data_devices[1];};
static unsigned freed,delivered;static u8 received[2048];static unsigned received_length;
static u16 get_unaligned_be16(const void *p){const u8*b=p;return b[0]*256+b[1];}
static void put_unaligned_le32(u32 v,void*p){memcpy(p,&v,4);}
static u32 readl(const void*p){u32 v;memcpy(&v,p,4);return v;}
static void writel(u32 v,void*p){memcpy(p,&v,4);}
static bool netif_carrier_ok(struct net_device*d){return d->carrier;}
static bool netif_running(struct net_device*d){return d->running;}
static void netif_stop_queue(struct net_device*d){d->stopped=true;}
static bool pskb_may_pull(struct sk_buff*s,unsigned n){return s->len>=n;}
static bool skb_vlan_tag_present(struct sk_buff*s){return s->tagged;}
static unsigned skb_vlan_tag_get(struct sk_buff*s){return s->vlan_tci;}
static void __vlan_hwaccel_clear_tag(struct sk_buff*s){s->tagged=false;}
static void skb_reset_mac_header(struct sk_buff*s){(void)s;}
static void skb_reset_mac_len(struct sk_buff*s){(void)s;}
struct ethhdr {u8 addresses[12];u16 h_proto;}__attribute__((packed));
static struct ethhdr *eth_hdr(struct sk_buff*s){return (void*)s->data;}
static void *skb_header_pointer(struct sk_buff*s,unsigned off,unsigned n,void*b){(void)b;return off+n<=s->len?s->data+off:NULL;}
static u8 ipv6_get_dsfield(const struct ipv6hdr*h){const u8*p=(const void*)h;return p[0]<<4|p[1]>>4;}
static int ipv6_skip_exthdr(struct sk_buff*s,int off,u8*n,__be16*f){(void)s;(void)off;(void)n;*f=0;return -1;}
static int __skb_vlan_pop(struct sk_buff*s,u16*tci){
 if(s->len<18)return -EINVAL;*tci=get_unaligned_be16(s->data+14);
 memmove(s->data+4,s->data,12);s->data+=4;s->len-=4;s->protocol=htons(get_unaligned_be16(s->data+12));return 0;
}
static int __vlan_insert_tag(struct sk_buff*s,u16 proto,u16 tci){
 s->data-=4;s->len+=4;memmove(s->data,s->data+4,12);memcpy(s->data+12,&proto,2);
 s->data[14]=tci>>8;s->data[15]=tci;return 0;
}
static void skb_postpush_rcsum(struct sk_buff*s,const void*p,unsigned n){(void)s;(void)p;(void)n;}
static void dev_kfree_skb_any(struct sk_buff*s){freed++;free(s);}
static void dev_consume_skb_any(struct sk_buff*s){dev_kfree_skb_any(s);}
static struct sk_buff *skb_clone(struct sk_buff*s,int flags){
 (void)flags;struct sk_buff*c=malloc(sizeof(*c));assert(c);*c=*s;c->data=c->memory+(s->data-s->memory);return c;
}
static int skb_copy_bits(struct sk_buff*s,unsigned off,void*p,unsigned n){assert(off+n<=s->len);memcpy(p,s->data+off,n);return 0;}
static void *skb_ext_add(struct sk_buff*s,int type){(void)s;(void)type;return NULL;}
static u16 eth_type_trans(struct sk_buff*s,struct net_device*d){(void)d;return htons(get_unaligned_be16(s->data+12));}
static void napi_gro_receive(struct napi_struct*n,struct sk_buff*s){(void)n;delivered++;received_length=s->len;memcpy(received,s->data,s->len);dev_kfree_skb_any(s);}
#define RTL_DMA_COUNT 64
#define RTL_DMA_BUFFER_SIZE 2048
struct rtl_dma_descriptor {u32 address,control;};
struct rtl_dma_tx {void *memory,*registers;u32 memory_dma;struct rtl_dma_descriptor *descriptors;unsigned read,write,vp,port,lengths[64];struct net_device*owners[64];};
static struct rtl_dma_tx dma;
static void rtl_dma_tx_complete(struct rtl_dma_tx*t,struct net_device*d){(void)t;(void)d;}
'''
model += '\n'.join(re.findall(r'^#define .*$', (drivers / 'rtl9617c-dma.c').read_text(), re.M)) + '\n'
model += function('rtl9617c-dma.h', 'rtl_dma_tx_available')
model += function('rtl9617c-dma.c', 'rtl_dma_vp')
model += function('rtl9617c-dma.c', 'rtl_dma_tx_frame')
model += r'''
static netdev_tx_t rtl9617c_ni_transmit(void*d,struct net_device*n,struct sk_buff*s,const struct rtl9617c_ni_frame*f){(void)d;return rtl_dma_tx_frame(&dma,s,n,f);}
'''
model += re.sub(r'^#include .*$', '', (drivers / 'rtl9617c-pon-vlan-skb.h').read_text(), flags=re.M)
model += function('rtl9617c-pon-path.c', 'rtl_pon_itu_service_entry')
model += function('rtl9617c-pon-path.c', 'rtl_pon_itu_data_transmit')
model += function('rtl9617c-pon-path.c', 'rtl_pon_itu_data_receive')
model += r'''
static struct sk_buff *packet(unsigned vid,bool metadata,unsigned code){
 struct sk_buff*s=calloc(1,sizeof(*s));assert(s);s->data=s->memory+128;s->len=40;
 memset(s->data,0xff,6);s->data[6]=2;s->data[11]=1;s->data[12]=0x88;s->data[13]=0x63;
 s->data[14]=0x11;s->data[15]=code;s->data[19]=20;
 for(unsigned i=20;i<40;i++)s->data[i]=i+73;
 if(vid){if(metadata){s->tagged=true;s->vlan_proto=htons(0x8100);s->vlan_tci=vid;}
 else __vlan_insert_tag(s,htons(0x8100),vid);}
 return s;
}
static void profile(struct rtl_evto_profile*p,unsigned from,unsigned to){
 *p=(struct rtl_evto_profile){.input_tpid=0x8100,.output_tpid=0x8100,.count=1};
 u32 words[]={0xf8000000,0x80004002|(from<<15),0x400f0000,0x80004|(to<<3)};
 for(unsigned i=0;i<4;i++)for(unsigned b=0;b<4;b++)p->row[0][i*4+b]=words[i]>>(24-b*8);
 assert(rtl_evto_valid(p));
}
int main(int argc,char**argv){
 bool retry=argc>1 && !strcmp(argv[1],"retry");
 struct net_device dev={.carrier=true,.running=true};struct napi_struct napi={};
 struct rtl_itu_path_state*state=calloc(1,sizeof(*state)+3*sizeof(struct rtl_evto_profile));assert(state);
 struct rtl9617c_pon pon={.itu_paths=state,.itu_paths_ready=true,.data_devices={&dev}};
 struct rtl_itu_paths*p=&state->paths;p->count=1;
 p->path[0]=(struct rtl_itu_path){.gem_id=1033,.index=8,.alloc_id=1032,.tcont=1,.vlan=46,.pbits=255,.downstream_pbits=255};
 memset(p->path[0].vlan_profile,255,sizeof(p->path[0].vlan_profile));
 dma.memory=calloc(1,MEMORY_BYTES);dma.registers=calloc(1,4096);dma.descriptors=dma.memory;dma.memory_dma=0x10000000;
 for(int rewrite=1;rewrite>=0;rewrite--)for(unsigned metadata=0;metadata<2;metadata++){
  p->profile_count=rewrite;p->path[0].vlan_profile[4]=rewrite?0:RTL_EVTO_NONE;profile(&p->profile[0],46,146);
  struct sk_buff*s=packet(46,metadata,9);unsigned before_length=s->len;u8 before[64];memcpy(before,s->data,s->len);
  dma.read=0;dma.write=retry?61:0;
  assert(rtl_pon_itu_data_transmit(&pon,s,&dev)==(retry?NETDEV_TX_BUSY:NETDEV_TX_OK));
  if(retry){
   assert(s->len==before_length && !memcmp(s->data,before,before_length) && s->tagged==!!metadata);
   dma.write=0;assert(rtl_pon_itu_data_transmit(&pon,s,&dev)==NETDEV_TX_OK);
  }
  const u8*payload=dma.memory+64*sizeof(struct rtl_dma_descriptor)+16;
  assert(dma.write==1 && readl(payload-8)==(BIT(30)|(8<<18)));
  assert((readl(payload-4)&0x7fff)==((16<<9)|(33<<3)));
  assert(((readl(payload-4)>>15)&0x3fff)==60);
  assert(get_unaligned_be16(payload+12)==0x8100 && get_unaligned_be16(payload+14)==(rewrite?146:46));
  assert(get_unaligned_be16(payload+16)==0x8863 && payload[19]==9);
  struct sk_buff*reply=packet(rewrite?146:46,false,7);
  rtl_pon_itu_data_receive(&pon,&napi,reply,7|(8<<8));
  assert(delivered==(1-rewrite)*2+metadata+1 && received_length==44);
  assert(get_unaligned_be16(received+12)==0x8100 && get_unaligned_be16(received+14)==46);
  assert(get_unaligned_be16(received+16)==0x8863 && received[19]==7);
  for(unsigned i=20;i<40;i++)assert(received[i+4]==i+73);
 }
 /* All three ANI paths are wildcard VIDs; mapper/IWTP EVTO selects the GEM. */
 const unsigned vids[]={41,45,46};
 p->count=p->profile_count=3;
 for(unsigned stage=3;stage<5;stage++)for(unsigned metadata=0;metadata<2;metadata++){
  for(unsigned i=0;i<3;i++){
   p->path[i]=(struct rtl_itu_path){.gem_id=1031+i,.index=8+i,.alloc_id=1032,.tcont=1,
    .vlan=RTL_ITU_VLAN_ANY,.pbits=255,.downstream_pbits=255};
   memset(p->path[i].vlan_profile,255,sizeof(p->path[i].vlan_profile));
   p->path[i].vlan_profile[stage]=i;profile(&p->profile[i],vids[i],vids[i]+100);
  }
  struct sk_buff*s=packet(46,metadata,9);dma.read=dma.write=0;
  assert(rtl_pon_itu_data_transmit(&pon,s,&dev)==NETDEV_TX_OK);
  const u8*payload=dma.memory+64*sizeof(struct rtl_dma_descriptor)+16;
  assert(readl(payload-8)==(BIT(30)|(10<<18)) && get_unaligned_be16(payload+14)==146 && payload[19]==9);
  unsigned before_delivered=delivered;
  rtl_pon_itu_data_receive(&pon,&napi,packet(146,false,7),7|(10<<8));
  assert(delivered==before_delivered+1 && get_unaligned_be16(received+14)==46 && received[19]==7);
 }
 puts("Three wildcard GEMs: mapper and IWTP profiles select VLAN46, edit wire VLAN146, restore PADO VLAN46");
 assert(!dev.stats.tx_dropped && !dev.stats.rx_dropped);
 /* A UNI rule that adds VLAN46 requires an untagged PPPoE interface. */
 p->count=p->profile_count=1;
 p->path[0].vlan=46;memset(p->path[0].vlan_profile,255,sizeof(p->path[0].vlan_profile));
 p->path[0].vlan_profile[0]=0;profile(&p->profile[0],46,46);
 u32 untagged[]={0xf8000000,0xf8000002,0x000f0000,4|(46<<3)};
 for(unsigned i=0;i<4;i++)for(unsigned b=0;b<4;b++)p->profile[0].row[0][i*4+b]=untagged[i]>>(24-b*8);
 dma.read=dma.write=0;
 assert(rtl_pon_itu_data_transmit(&pon,packet(0,false,9),&dev)==NETDEV_TX_OK && dma.write==1);
 const u8*payload=dma.memory+64*sizeof(struct rtl_dma_descriptor)+16;
 assert(get_unaligned_be16(payload+14)==46 && payload[19]==9);
 unsigned before_delivered=delivered;
 rtl_pon_itu_data_receive(&pon,&napi,packet(46,false,7),7|(8<<8));
 assert(delivered==before_delivered+1 && received_length==40 && received[15]==7);
 assert(rtl_pon_itu_data_transmit(&pon,packet(46,false,9),&dev)==NETDEV_TX_OK);
 assert(dma.write==1 && dev.stats.tx_dropped==1);
 puts("UNI untagged-to-VLAN46: plain PADI and untagged PADO pass; pretagged PADI is rejected by the OLT rule");
 /* The OLT's default UNI rules discard tagged frames. A local VLAN WAN is
  * already on the service side; ANI/IWTP edits still apply in both directions. */
 u32 defaults[3][4]={{0xe8000000,0xe8000000,0xc00f0000,0x000f0000},
  {0xf8000000,0xe8000000,0xc00f0000,0x000f0000},
  {0xf8000000,0xf8000000,0x000f0000,0x000f0000}};
 p->profile_count=2;p->profile[0].count=3;
 for(unsigned r=0;r<3;r++)for(unsigned i=0;i<4;i++)for(unsigned b=0;b<4;b++)
  p->profile[0].row[r][i*4+b]=defaults[r][i]>>(24-b*8);
 assert(rtl_evto_valid(&p->profile[0]));
 p->path[0].vlan_profile[4]=1;profile(&p->profile[1],46,146);
 struct net_device vlan={.vlan_proto=htons(0x8100),.vlan_id=46};dev.upper=&vlan;
 for(unsigned metadata=0;metadata<2;metadata++){
  unsigned tx_drops=dev.stats.tx_dropped,rx_drops=dev.stats.rx_dropped,before=delivered;
  dma.read=dma.write=0;
  assert(rtl_pon_itu_data_transmit(&pon,packet(46,metadata,9),&dev)==NETDEV_TX_OK);
  payload=dma.memory+64*sizeof(struct rtl_dma_descriptor)+16;
  assert(dma.write==1 && get_unaligned_be16(payload+14)==146 && dev.stats.tx_dropped==tx_drops);
  rtl_pon_itu_data_receive(&pon,&napi,packet(146,metadata,7),7|(8<<8));
  assert(delivered==before+1 && received_length==44 && get_unaligned_be16(received+14)==46 && dev.stats.rx_dropped==rx_drops);
 }
 p->profile[0].downstream=8;
 for(unsigned role=0;role<3;role++){
  vlan.bridge_port=role==0;dev.bridge_port=role==1;vlan.vlan_id=role==2?47:46;
  unsigned tx_drops=dev.stats.tx_dropped,rx_drops=dev.stats.rx_dropped,before=delivered;
  dma.read=dma.write=0;
  assert(rtl_pon_itu_data_transmit(&pon,packet(46,false,9),&dev)==NETDEV_TX_OK);
  rtl_pon_itu_data_receive(&pon,&napi,packet(146,false,7),7|(8<<8));
  assert(!dma.write && dev.stats.tx_dropped==tx_drops+1 && dev.stats.rx_dropped==rx_drops+1 && delivered==before);
 }
 puts("Local VLAN service: UNI tagged-drop bypassed, IWTP rewrite preserved; bridge ports and unconfigured VIDs retain UNI policy");
 free(dma.memory);free(dma.registers);free(state);
 puts(retry?"PADI DMA BUSY retry preserves original SKB and single VLAN edit; PADO return passed":"PADI VLAN46/GEM1033/TCONT1/queue0 -> DMA bytes; PADO GEM8 -> VLAN46; inline/metadata and IWTP rewrite passed");
}
'''
with tempfile.TemporaryDirectory(prefix='itu-pppoe-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(model)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-function', '-Wno-misleading-indentation',
                    '-fsanitize=address,undefined', '-I' + str(drivers),
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
    subprocess.run([str(work / 'test'), 'retry'], check=True)
