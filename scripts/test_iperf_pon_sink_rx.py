#!/usr/bin/env python3
"""Exercise PPPoE removal and UDP/TCP delivery using the actual sink callback."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'tools/iperf-pon/iperf_pon_sink.c').read_text()
start = source.index('static void receive(')
end = source.index('\nstatic netdev_tx_t discard(', start)
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>
typedef unsigned char u8;
#define ETH_HLEN 14
#define ETH_P_PPP_SES 0x8864
#define ETH_P_IP 0x800
#define CHECKSUM_NONE 0
#define CHECKSUM_UNNECESSARY 1
#define min_t(t,a,b) ((a)<(b)?(a):(b))
struct sk_buff {u8 *data;unsigned len;int ip_summed,protocol;void *dev;unsigned gso_segs;};
typedef uint64_t u64;
typedef int rx_handler_result_t;
#define RX_HANDLER_PASS 0
#define DEFINE_PER_CPU(type,name) type name[2]
static _Thread_local unsigned cpu;
#define this_cpu_inc(name) ((name)[cpu]++)
#define this_cpu_add(name,n) ((name)[cpu]+=(n))
#define skb_shinfo(s) (s)
struct rtl9617c_ni_wifi_endpoint {int unused;};
static void *sink=(void *)1,*ni_napi=(void *)2;
static unsigned received,received_bytes,bad_pppoe,plain,gro,freed,expected_checksum;
static bool udp,udp_gro;static u8 first[32];
static unsigned get_unaligned_be16(const u8 *p){return p[0]*256+p[1];}
static void put_unaligned_be16(unsigned v,u8 *p){p[0]=v>>8;p[1]=v;}
static void skb_pull(struct sk_buff *s,unsigned n){s->data+=n;s->len-=n;}
static int eth_type_trans(struct sk_buff *s,void *dev){
 assert(dev==sink && s->data[12]==8 && !s->data[13]);
 skb_pull(s,14);return ETH_P_IP;
}
static void skb_unset_transport_header(struct sk_buff *s){}
static void netif_receive_skb(struct sk_buff *s){plain++;assert(s->ip_summed==expected_checksum);}
static void napi_gro_receive(void *n,struct sk_buff *s){gro++;assert(n==ni_napi && s->ip_summed==expected_checksum);}
static void dev_kfree_skb_any(struct sk_buff *s){freed++;}
'''
checks = r'''
static void *count_worker(void *arg){
 cpu=(unsigned)(uintptr_t)arg;
 struct sk_buff skb={.gso_segs=cpu?7:1};struct sk_buff *p=&skb;
 for(unsigned i=0;i<250000;i++)assert(count_stack(&p)==RX_HANDLER_PASS);
 return 0;
}
int main(void){
 u8 raw[128],original[128];
 for(int mode=0;mode<8;mode++){
  udp=mode & 1;udp_gro=mode & 2;
  expected_checksum=(mode&4)?CHECKSUM_UNNECESSARY:CHECKSUM_NONE;
  for(unsigned i=0;i<sizeof(raw);i++)raw[i]=i;
  put_unaligned_be16(ETH_P_PPP_SES,raw+12);
  raw[14]=0x11;raw[15]=0;put_unaligned_be16(0x1234,raw+16);
  put_unaligned_be16(sizeof(raw)-20,raw+18);put_unaligned_be16(0x21,raw+20);
  memcpy(original,raw,sizeof(raw));
  struct sk_buff s={raw,sizeof(raw),(mode&4)?CHECKSUM_UNNECESSARY:7,0,0};receive(0,&s,true);
  assert(s.data==raw+22 && s.len==sizeof(raw)-22 && s.protocol==ETH_P_IP);
  assert(!memcmp(s.data,original+22,s.len));
  assert(!memcmp(raw+8,original,12));
 }
 assert(received==8 && received_bytes==1024 && plain==2 && gro==6 && !freed);
 for(unsigned n=0;n<22;n++){
  struct sk_buff s={raw,n,0,0,0};receive(0,&s,true);
 }
 memcpy(raw,original,sizeof(raw));raw[16]=0;
 struct sk_buff s={raw,sizeof(raw),0,0,0};receive(0,&s,true);
 assert(freed==23 && bad_pppoe==23 && plain==2 && gro==6);
 pthread_t workers[2];
 for(unsigned i=0;i<2;i++)assert(!pthread_create(&workers[i],0,count_worker,(void *)(uintptr_t)i));
 for(unsigned i=0;i<2;i++)assert(!pthread_join(workers[i],0));
 assert(stack_skbs[0]+stack_skbs[1]==500000);
 assert(gro_skbs[0]+gro_skbs[1]==250000);
 assert(gro_frames[0]+gro_frames[1]==1750000);
}
'''
with tempfile.TemporaryDirectory(prefix='iperf-sink-rx-') as directory:
    p = Path(directory)
    counters = source[source.index('static DEFINE_PER_CPU'):source.index('struct sink_key')]
    (p / 'test.c').write_text(fixture + counters + source[start:end] + checks)
    subprocess.run(['cc', '-O1', '-g', '-pthread', '-fsanitize=address,undefined',
                    str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
print('iperf sink: PPPoE/payload preserved, UDP direct/RPS default, opt-in UDP GRO, TCP GRO, bad headers dropped, concurrent per-CPU counts exact')
