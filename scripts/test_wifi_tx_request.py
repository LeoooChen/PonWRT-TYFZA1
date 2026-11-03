#!/usr/bin/env python3
"""Compare cached PE request construction against direct DDR stores."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
temporary = tempfile.TemporaryDirectory(prefix='wifi-tx-request-')
p = Path(temporary.name)
current = (driver / 'tx.c').read_text()
old = current.replace('u32 request[8 + RTL_AX_TX_WP_ADDRESS_MAX * 2];\n\t\tu32 *published =', 'u32 *request =')
assert old != current
publish = re.search(r'\t\t/\* Build in cached memory,.*?\*/\n\t\tmemcpy\(published, request, \(8 \+ segments \* 2\) \* sizeof\(\*request\)\);\n', old, re.S)
assert publish
old = old[:publish.start()] + old[publish.end():]
(p / 'old.c').write_text(old)


def branch(path):
    source = path.read_text()
    start = source.index('int rtl_ax_tx_submit(')
    start = source.index('\tif (tx->pe_active) {', start)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define RTL_AX_TX_WP_ADDRESS_MAX 10
#define READ_ONCE(x) (x)
#define ETH_HLEN 14
#define IEEE80211_TX_CTL_NO_ACK 1
#define IEEE80211_KEY_FLAG_PAIRWISE 1
#define le16_to_cpu(x) (x)
#define lower_32_bits(x) ((u32)(x))
#define atomic64_inc(p) (++*(p))
#define atomic64_inc_return(p) (++*(p))
struct key {unsigned flags,hw_key_idx,keyidx;u64 tx_pn;};
struct info {unsigned flags;struct {struct key *hw_key;}control;};
struct hdr {uint16_t frame_control,seq_ctrl;u8 addr1[6];};
struct skb {u8 *data;unsigned len;};
struct scatterlist {u32 dma,len;};
struct mapping {unsigned dma_count;struct scatterlist sg[10];};
struct slot {struct mapping *mapping;u32 dma;};
struct tx {bool pe_active,checksum_enabled;struct {void *memory;}pe;u8 *headers;unsigned pe_producer,producer[6];
 u32 peer_rates[128];u64 hardware_pn,encrypted,pe_ethernet,scatter_packets,scatter_segments;
 u32 last_eth_dma;u8 last_eth[14];};
static unsigned get_unaligned_le32(const void*p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void*p){memcpy(p,&v,4);}
static bool ieee80211_is_data(unsigned fc){return fc&8;}
static bool is_multicast_ether_addr(const u8*p){return *p&1;}
#define for_each_sg(list,sg,n,i) for((i)=0;(i)<(n)&&((sg)=&(list)[i],1);(i)++)
#define sg_dma_address(s) ((s)->dma)
#define sg_dma_len(s) ((s)->len)
'''
signature = r'''
static void NAME(struct tx*tx,struct info*info,struct hdr*header,struct skb*skb,struct slot slot,
 unsigned id,unsigned ring,unsigned next,unsigned packet_bytes,unsigned header_bytes,unsigned macid,
 unsigned rate,unsigned gi,unsigned bandwidth,unsigned tid,unsigned aggregate,unsigned density,
 bool encap,bool pe_sequence,unsigned original_header_bytes,const u8*original_header){
 static const u8 tid_indicators[]={0,0,1,1,0,1,0,1};
'''
code += signature.replace('NAME', 'old') + branch(p/'old.c') + '\n}\n'
code += signature.replace('NAME', 'candidate') + branch(driver/'tx.c') + '\n}\n'
code += r'''
int main(void){
 static u8 mem[2][0x20000],headers[2][256*128],payload[2048];
 unsigned vectors=0;
 for(unsigned layout=0;layout<=10;layout++)
 for(unsigned encap=0;encap<2;encap++)for(unsigned seq=0;seq<2;seq++)
 for(unsigned keytype=0;keytype<3;keytype++)for(unsigned tid=0;tid<8;tid++){
  struct tx a={.pe_active=true,.checksum_enabled=tid&1,.pe={mem[0]},.headers=headers[0],.pe_producer=255};
  struct tx b=a; b.pe.memory=mem[1];b.headers=headers[1];
  memset(mem,0xa5,sizeof(mem));memset(headers,0xa6,sizeof(headers));memset(payload,0xaa,sizeof(payload));
  struct mapping mapping={.dma_count=layout};for(unsigned n=0;n<layout;n++)mapping.sg[n]=(struct scatterlist){0x120380be + n*4096,1500+n};
  struct slot slot={.mapping=layout?&mapping:NULL,.dma=0x11000000};
  struct key ka={.flags=keytype==2,.hw_key_idx=126,.keyidx=3,.tx_pn=0xffffffffULL},kb=ka;
  struct info ia={.flags=tid&1,.control={keytype?&ka:NULL}},ib=ia;ib.control.hw_key=keytype?&kb:NULL;
  struct hdr hdr={.frame_control=8,.seq_ctrl=0xffff,.addr1={tid&1}};
  struct skb skb={payload,sizeof(payload)};
  unsigned macid=127,ring=tid%6;
  a.peer_rates[macid]=b.peer_rates[macid]=(tid&1?BIT(31):0)|BIT(21)|BIT(22)|0x1ff;
  a.producer[ring]=b.producer[ring]=255;
  put_unaligned_le32(tid&1?BIT(17):0,mem[0]+0xe200+macid*4);
  put_unaligned_le32(tid&1?BIT(17):0,mem[1]+0xe200+macid*4);
  old(&a,&ia,&hdr,&skb,slot,255,ring,0,3000,26,macid,0x1ab,3,3,tid,64,7,encap,seq,encap?14:seq?26:0,payload);
  candidate(&b,&ib,&hdr,&skb,slot,255,ring,0,3000,26,macid,0x1ab,3,3,tid,64,7,encap,seq,encap?14:seq?26:0,payload);
  assert(!memcmp(mem[0],mem[1],sizeof(mem[0]))&&!memcmp(headers[0],headers[1],sizeof(headers[0])));
  b.pe.memory=a.pe.memory;b.headers=a.headers;assert(!memcmp(&a,&b,sizeof(a))&&!memcmp(&ka,&kb,sizeof(ka)));vectors++;
 }
 printf("Actual PE request branch: %u vectors old/cached, DMA layouts0..10, keys/PN carry, raw/encap, sequence, rate overrides, all TIDs and ring wrap PASS\n",vectors);
}
'''
(p/'test_request.c').write_text(code)
subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test_request.c'),'-o',str(p/'test_request')],check=True)
subprocess.run([str(p/'test_request')],check=True)
s=(driver/'tx.c').read_text();a=s.index('int rtl_ax_tx_submit(');s=s[a:s.index('\nstatic void rtl_ax_tx_pe_complete',a)]
assert s.index('memcpy(published, request,') < s.index('tx->slots[id] = slot;') < s.index('\n\tdma_wmb();') < s.index('put_unaligned_le32(tx->pe_producer,')
print('Request copy precedes slot ownership, existing DMA barrier and producer publication')

temporary.cleanup()
