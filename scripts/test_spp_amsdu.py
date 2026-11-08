#!/usr/bin/env python3
"""Execute Linux's shared CCM/GCM AAD builder with negotiated SPP A-MSDU."""
from pathlib import Path
import subprocess, tempfile

root=Path(__file__).resolve().parents[1]
kernel=next((root/'sources/openwrt/build_dir').glob('target-*/linux-rtl9617c_generic/linux-6.18.55'))
source=(kernel/'net/mac80211/wpa.c').read_text()
start=source.index('static u8 ccmp_gcmp_aad(')
end=source.index('\nstatic void ccmp_special_blocks(',start)
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint16_t __le16;
#define ETH_ALEN 6
#define IEEE80211_QOS_CTL_LEN 2
#define IEEE80211_QOS_CTL_TID_MASK 15
#define IEEE80211_QOS_CTL_A_MSDU_PRESENT 128
#define IEEE80211_FCTL_RETRY 0x0800
#define IEEE80211_FCTL_PM 0x1000
#define IEEE80211_FCTL_MOREDATA 0x2000
#define IEEE80211_FCTL_PROTECTED 0x4000
#define IEEE80211_FCTL_ORDER 0x8000
#define cpu_to_le16(v) (v)
struct ieee80211_hdr {u16 frame_control,duration;union {u8 addrs[18];struct {u8 addr1[6],addr2[6],addr3[6];};};u16 seq_ctrl;u8 addr4[6];};
struct sk_buff {u8 *data;};
static bool ieee80211_is_mgmt(u16 fc){return !(fc&12);}
static bool ieee80211_has_a4(u16 fc){return (fc&0x300)==0x300;}
static bool ieee80211_is_data_qos(u16 fc){return (fc&0x8c)==0x88;}
static u8 *ieee80211_get_qos_ctl(struct ieee80211_hdr *h){return (u8*)h+(ieee80211_has_a4(h->frame_control)?30:24);}
static void put_unaligned_be16(u16 v,void *p){((u8*)p)[0]=v>>8;((u8*)p)[1]=v;}
static void put_unaligned(u16 v,void *p){memcpy(p,&v,2);}
''' + source[start:end] + r'''
int main(void){
 unsigned cases=0;
 for(unsigned ds=1;ds<=3;ds++)for(unsigned tid=0;tid<16;tid++)for(unsigned qos=0;qos<2;qos++)
 for(unsigned amsdu=0;amsdu<2;amsdu++)for(unsigned spp=0;spp<2;spp++) {
  u8 data[40]={},aad[32],plain[32];struct ieee80211_hdr *h=(void*)data;struct sk_buff skb={data};
  h->frame_control=(qos?0x88:8)|ds<<8|0xf800;h->seq_ctrl=0xabc5;
  for(unsigned i=0;i<18;i++)h->addrs[i]=i+1;
  if(ds==3)for(unsigned i=0;i<6;i++)h->addr4[i]=i+41;
  if(qos)*ieee80211_get_qos_ctl(h)=tid|0x60|(amsdu?128:0);
  u8 actual=ccmp_gcmp_aad(&skb,aad,spp);
  ccmp_gcmp_aad(&skb,plain,false);
  unsigned offset=ds==3?30:24;
  assert(actual==(qos?(tid|(spp&&amsdu?128:0)):0));
  assert(aad[offset]==actual&&aad[22]==5&&aad[23]==0);
  for(unsigned i=0;i<32;i++)if(i!=offset)assert(aad[i]==plain[i]);
  assert((aad[offset]^plain[offset])==(qos&&spp&&amsdu?128:0));
  /* Retry/power-save/order flags do not alter the authenticated SPP bit. */
  h->frame_control^=0x3800;u8 retry[32];ccmp_gcmp_aad(&skb,retry,spp);
  assert(!memcmp(aad,retry,32));cases++;
 }
 printf("SPP A-MSDU: %u actual Linux CCM/GCM AAD cases passed\n",cases);
}
'''
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);(p/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
