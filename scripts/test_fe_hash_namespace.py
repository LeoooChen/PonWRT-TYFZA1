#!/usr/bin/env python3
"""Reproduce cross-template hits using production flow keys and zero VIF metadata."""
from pathlib import Path
import argparse
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision', help='Check an older source revision against the same contract')
args = parser.parse_args()
def read(name):
    path = drivers / name
    return subprocess.check_output(['git', 'show', args.revision + ':' + str(path.relative_to(root))], text=True) if args.revision else path.read_text()
source = read('rtl9617c-fe.c').split('static int fe_install(', 1)[1]
key_code = source[source.index(' if(data.bridge)'):source.index(' start=rtl_fe_hash_bucket')]
common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include "rtl9617c-fe-format.h"
#define VLAN_CFI_MASK 0x1000
#define VLAN_PRIO_SHIFT 13
struct rtl_fe_rule {
 bool bridge,ipv6,vlan_exact,pppoe_input;
 unsigned char source[6],destination[6],ppp_protocol_slot;
 unsigned int ethernet_protocol,vlan_count;
 unsigned short ingress_tci[2],pppoe_session;
 struct rtl_fe_vlan vlan[2];
 struct rtl_fe_ipv4_key key;
 struct rtl_fe_ipv6_key key6;
};
struct rtl_fe_port {unsigned int wifi_id;};
static void installed_key(struct rtl_fe_rule data,unsigned int words[23])
{
 struct rtl_fe_port input={0},*port=&input;
 unsigned int i,crc32;unsigned short crc16;
'''
test = r'''
 (void)crc32;(void)crc16;
}
static unsigned int get(const unsigned int *words,unsigned int bit,unsigned int width)
{
 unsigned int result=0;
 for(unsigned int i=0;i<width;i++)result|=((words[(bit+i)/32]>>((bit+i)%32))&1)<<i;
 return result;
}
int main(void)
{
 unsigned int masks[8][8],stored[23],packet[23],c32,other32;
 unsigned short c16,other16;
 rtl_fe_ipv6_mask(masks[0]);
 for(unsigned int i=1;i<8;i++){
  if(i<2)rtl_fe_ipv6_mask(masks[i]);else rtl_fe_bridge_mask(masks[i]);
  if(i&1)rtl_fe_wifi_mask(masks[i]);
 }
 for(unsigned int i=0;i<8;i++)assert(!get(masks[i],217,1));
 struct rtl_fe_rule data={.key={.source=0xc0a801ac,.destination=0x74e46f76,
  .source_port=39741,.destination_port=53,.protocol=17,.ingress=1,.mask=63}};
 installed_key(data,stored);
 /* Hardware fixture: both templates see identical fields when VIF=0.
  * The mask ID must keep the higher-priority Wi-Fi lookup out of this flow. */
 for(unsigned int i=0;i<2;i++){
  data.key.mask=63-i;rtl_fe_ipv4_key(packet,&data.key);
  rtl_fe_key_set(packet,RTL_FE_KEY_ORIG_LSPID,0);
  rtl_fe_key_set(packet,RTL_FE_KEY_TABLE_ID,0);
  if(get(masks[i],217,1))rtl_fe_key_set(packet,RTL_FE_KEY_HKEY_ID,0);
  rtl_fe_key_crc(packet,&c32,&c16);
  if(!i){rtl_fe_key_crc(stored,&other32,&other16);assert(c32==other32 && c16==other16);}
  else assert(c32!=other32);
 }
 for(unsigned int n=0;n<128;n++){
  data.ipv6=n&1;data.bridge=false;
  data.key.source_port=1024+n;data.key.protocol=n&2?6:17;
  data.key6=(struct rtl_fe_ipv6_key){.source={0x20010db8,0,1,n+1},.destination={0x20010db8,0,2,n+2},
   .source_port=1024+n,.destination_port=53,.protocol=n&2?6:17,.ingress=1};
  for(unsigned int i=0;i<8;i++){
   data.bridge=i>=2;data.key.mask=data.key6.mask=63-i;
   installed_key(data,stored);assert(get(stored,718,6)==63-i);
  }
 }
 puts("FE Hash namespaces: zero-VIF cross-hit regression and 1024 route/bridge keys passed");
}
'''
with tempfile.TemporaryDirectory(prefix='fe-namespace-', dir=root / 'artifacts') as tmp:
    tmp = Path(tmp)
    (tmp / 'rtl9617c-fe-format.h').write_text(read('rtl9617c-fe-format.h'))
    (tmp / 'test.c').write_text(common + key_code + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O2', '-I', str(tmp), str(tmp / 'test.c'), '-o', str(tmp / 'test')], check=True)
    subprocess.run([str(tmp / 'test')], check=True)
