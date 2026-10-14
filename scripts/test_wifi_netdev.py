#!/usr/bin/env python3
"""Execute production Wi-Fi ingress/return functions with captured Ethernet frames."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-wifi.c').read_text()

def function(name):
    start = re.search(r'^static .*\b' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/ppp_defs.h>
typedef uint8_t u8;typedef uint16_t __be16;
#define ETH_HLEN 14
#define VLAN_HLEN 4
#define ETH_P_IP 0x800
#define ETH_P_IPV6 0x86dd
#define ETH_P_PPP_SES 0x8864
#define CHECKSUM_NONE 0
#define CHECKSUM_PARTIAL 3
#define GFP_ATOMIC 0
#define NF_ACCEPT 1
#define NF_STOLEN 2
struct net_device {int unused;};
struct sk_buff {
 unsigned char *head,*data;unsigned int len,mac,network,priority,ip_summed;
 unsigned short protocol,vlan;
 bool rtl9617c_wifi_miss,gso;
 struct net_device *dev;
};
struct nf_hook_state {int unused;};
struct rtl9617c_ni_wifi_endpoint {struct net_device *netdev;};
struct rtl_wifi_port {unsigned int id;};
struct ethhdr {unsigned char h_dest[6],h_source[6];unsigned short h_proto;} __attribute__((packed));
static int wifi_ni,enabled=1,busy,allocation_failure;
static unsigned int frees,injected,transmitted,returned,id_seen,frame_length,network_seen;
static unsigned char frame_seen[2048];
static struct sk_buff *new_skb(const unsigned char *frame,unsigned int len,bool ethernet){
 struct sk_buff *s=calloc(1,sizeof(*s));assert(s);
 s->head=calloc(1,len+64);assert(s->head);memcpy(s->head+34,frame,len);
 s->mac=34;s->data=s->head+34+(ethernet?0:14);s->len=len-(ethernet?0:14);
 memcpy(&s->protocol,frame+12,2);return s;
}
static void kfree_skb(struct sk_buff *s){frees++;free(s->head);free(s);}
static void consume_skb(struct sk_buff *s){kfree_skb(s);}
static bool rtl9617c_ni_wifi_enabled(void *dev){(void)dev;return enabled;}
static bool skb_is_gso(struct sk_buff *s){return s->gso;}
static unsigned int skb_mac_header_len(struct sk_buff *s){return s->data-(s->head+s->mac);}
static struct ethhdr *eth_hdr(struct sk_buff *s){return (void *)(s->head+s->mac);}
static bool is_multicast_ether_addr(unsigned char *a){return *a&1;}
static unsigned int get_unaligned_be16(const void *p){unsigned short v;memcpy(&v,p,2);return ntohs(v);}
static void *skb_header_pointer(struct sk_buff *s,unsigned int off,unsigned int len,void *p){
 (void)p;return off+len<=s->len?s->data+off:NULL;
}
static void *skb_mac_header(struct sk_buff *s){return s->head+s->mac;}
static struct sk_buff *skb_copy_expand(struct sk_buff *s,unsigned int head,unsigned int tail,int flags){
 (void)head;(void)tail;(void)flags;if(allocation_failure)return NULL;
 struct sk_buff *copy=new_skb(s->head+s->mac,s->len+14,false);
 copy->dev=s->dev;copy->vlan=s->vlan;copy->priority=s->priority;copy->ip_summed=s->ip_summed;return copy;
}
static void skb_push(struct sk_buff *s,unsigned int len){s->data-=len;s->len+=len;}
static void skb_pull(struct sk_buff *s,unsigned int len){s->data+=len;s->len-=len;}
static void skb_reset_mac_header(struct sk_buff *s){s->mac=s->data-s->head;}
static void skb_set_network_header(struct sk_buff *s,unsigned int len){s->network=s->data-s->head+len;}
static bool skb_vlan_tag_present(struct sk_buff *s){return s->vlan!=0;}
static struct sk_buff *__vlan_hwaccel_push_inside(struct sk_buff *s){
 memmove(s->data+16,s->data+12,s->len-12);
 unsigned short p=htons(0x8100),v=htons(s->vlan);memcpy(s->data+12,&p,2);memcpy(s->data+14,&v,2);
 s->vlan=0;s->len+=4;return s;
}
static unsigned short __vlan_get_protocol(struct sk_buff *s,unsigned short proto,int *depth){
 while(proto==htons(0x8100) || proto==htons(0x88a8)){
  memcpy(&proto,s->data+*depth+2,2);*depth+=4;
 }
 return proto;
}
static int rtl9617c_ni_wifi_lookup(void *dev,struct sk_buff *s,unsigned int id){
 (void)dev;if(busy)return -EBUSY;
 injected++;id_seen=id;frame_length=s->len;memcpy(frame_seen,s->data,s->len);kfree_skb(s);return 0;
}
static void dev_queue_xmit(struct sk_buff *s){
 transmitted++;frame_length=s->len;network_seen=s->network-(s->data-s->head);
 memcpy(frame_seen,s->data,s->len);assert(s->ip_summed==CHECKSUM_NONE);kfree_skb(s);
}
static unsigned short eth_type_trans(struct sk_buff *s,struct net_device *dev){
 unsigned short p=eth_hdr(s)->h_proto;s->data+=14;s->len-=14;s->dev=dev;return p;
}
static void netif_receive_skb(struct sk_buff *s){
 assert(s->rtl9617c_wifi_miss && skb_mac_header_len(s)==14);returned++;
 frame_length=s->len+14;memcpy(frame_seen,s->head+s->mac,s->len+14);kfree_skb(s);
}
'''.replace('static int wifi_ni,enabled', 'static void *wifi_ni;static int enabled')

records = []
for match in re.finditer(r'phase=hit.*?frame=([0-9a-f]{120})',
                         (root / 'logs/wifi-static-lifecycle-20261010.txt').read_text()):
    records.append(bytes.fromhex(match[1]))
assert len(records) == 8
arrays = 'static const unsigned char frames[8][60]={' + ','.join(
    '{' + ','.join(str(x) for x in frame) + '}' for frame in records) + '};\n'
test = r'''
int main(void){
 struct net_device dev={};struct rtl9617c_ni_wifi_endpoint endpoint={&dev};
 struct rtl_wifi_port port={.id=2};struct nf_hook_state state={};
 for(unsigned int i=0;i<8;i++){
  struct sk_buff *skb=new_skb(frames[i],60,false);skb->dev=&dev;
  assert(wifi_ingress(&port,skb,&state)==NF_STOLEN);
  assert(id_seen==2 && frame_length==60 && !memcmp(frame_seen,frames[i],60));
  skb=new_skb(frames[i],60,true);skb->dev=&dev;wifi_receive(&endpoint,skb,true);
  assert(network_seen==14 && frame_length==60 && !memcmp(frame_seen,frames[i],60));
  skb=new_skb(frames[i],60,true);skb->dev=&dev;wifi_receive(&endpoint,skb,false);
  assert(frame_length==60 && !memcmp(frame_seen,frames[i],60));
 }
 assert(injected==8 && transmitted==8 && returned==8 && frees==24);
 struct sk_buff *skb=new_skb(frames[0],60,false);skb->dev=&dev;unsigned int old=injected;
 skb->rtl9617c_wifi_miss=true;
 assert(wifi_ingress(&port,skb,&state)==NF_ACCEPT && !skb->rtl9617c_wifi_miss && injected==old);
 busy=1;assert(wifi_ingress(&port,skb,&state)==NF_ACCEPT && injected==old);busy=0;
 assert(skb->len==46 && !memcmp(skb->head+skb->mac,frames[0],60));
 enabled=0;assert(wifi_ingress(&port,skb,&state)==NF_ACCEPT);enabled=1;
 skb->gso=true;assert(wifi_ingress(&port,skb,&state)==NF_ACCEPT);skb->gso=false;
 skb->ip_summed=3;assert(wifi_ingress(&port,skb,&state)==NF_ACCEPT);skb->ip_summed=0;
 skb->vlan=123;allocation_failure=1;assert(wifi_ingress(&port,skb,&state)==NF_ACCEPT);allocation_failure=0;
 skb->vlan=123;assert(wifi_ingress(&port,skb,&state)==NF_STOLEN);
 assert(frame_length==64 && frame_seen[12]==0x81 && frame_seen[13]==0 && frame_seen[14]==0 && frame_seen[15]==123);
 assert(!memcmp(frame_seen+16,frames[0]+12,48));
 unsigned char tagged[68];memcpy(tagged,frames[0],12);
 memcpy(tagged+12,"\x88\xa8\x00\x7b\x81\x00\x00\x7c",8);memcpy(tagged+20,frames[0]+12,48);
 skb=new_skb(tagged,68,true);skb->dev=&dev;wifi_receive(&endpoint,skb,true);
 assert(network_seen==22 && frame_length==68 && !memcmp(frame_seen,tagged,68));
 unsigned char ppp[70]={};memcpy(ppp,frames[0],12);
 memcpy(ppp+12,"\x88\x64\x11\0\x12\x34\0\x30\0\x21",10);
 memcpy(ppp+22,frames[0]+14,46);
 skb=new_skb(ppp,68,false);skb->dev=&dev;
 assert(wifi_ingress(&port,skb,&state)==NF_STOLEN);
 assert(frame_length==68 && !memcmp(frame_seen,ppp,68));
 memset(ppp+22,0,48);ppp[22]=0x60;ppp[28]=17;ppp[19]=50;ppp[21]=0x57;
 skb=new_skb(ppp,70,false);skb->dev=&dev;
 assert(wifi_ingress(&port,skb,&state)==NF_STOLEN);
 assert(frame_length==70 && !memcmp(frame_seen,ppp,70));
 ppp[20]=0xc0;ppp[21]=0x21;
 skb=new_skb(ppp,70,false);skb->dev=&dev;
 assert(wifi_ingress(&port,skb,&state)==NF_ACCEPT);kfree_skb(skb);
 puts("Wi-Fi netdev: Ethernet/VLAN/QinQ and PPPoE IPv4/IPv6 bytes preserved; LCP stays with Linux");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-netdev-', dir=root / 'artifacts') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(common + function('wifi_ingress') + function('wifi_receive') + arrays + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-g', '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
(root / 'logs/wifi-netdev-contract.json').write_text(json.dumps({
    'production_source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'captured_hardware_frames': len(records), 'actual_functions': ['wifi_ingress', 'wifi_receive'],
    'single_miss_reinjection': True, 'full_Ethernet_TX': True, 'VLAN_QinQ': True,
    'busy_retains_original': True, 'sanitizers': ['ASan', 'UBSan'], 'real_AP_traffic_test': False,
}, indent=2) + '\n')
