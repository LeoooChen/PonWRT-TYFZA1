#!/usr/bin/env python3
"""Compare Wi-Fi registration's FE key with an actual NI parser/SWO capture."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = Path(sys.argv[1]) if len(sys.argv) > 1 else driver / 'rtl9617c-fe.c'
text = source.read_text()
body = text[text.index('int rtl_fe_add_wifi_port('):text.index('\nbool rtl_fe_enabled(')]
harness = r'''
#include <assert.h>
#include <stdio.h>
#include "rtl9617c-fe-format.h"
#define RTL_WIFI_SOURCE 18U
struct rtl_fe { int lock; };
struct net_device { int unused; };
struct rtl_fe_port { unsigned int wifi_id,llid; };
static struct rtl_fe_port port;
static int present;
static void mutex_lock(int *lock) {(void)lock;}
static void mutex_unlock(int *lock) {(void)lock;}
static struct rtl_fe_port *fe_port(struct rtl_fe *fe,struct net_device *dev)
{(void)fe;(void)dev;return present?&port:0;}
static int rtl_fe_add_port(struct rtl_fe *fe,struct net_device *dev,unsigned int src,unsigned int dst,unsigned int pol)
{(void)fe;(void)dev;assert(src==18 && dst==16);port.llid=pol;present=1;return 0;}
'''
harness += body + r'''
int main(void) {
 /* Real IPv4 uplink, Header-I -> hardware SWO, mask58.
  * Before the fix only key bit682 differs: POL_ID1 versus software0. */
 const unsigned int captured[23]={0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
  0xb4241000,0x8de06be5,0x1674b000,0x0000e04c,0x00000040,0,0x00000400,0x000e9200};
 const unsigned char src[]={0x46,0xf0,0x35,0xf2,0xda,0x12},dst[]={0,0xe0,0x4c,0x16,0x74,0xb0};
 struct rtl_fe fe={};struct net_device dev={};unsigned int key[23],crc;unsigned short crc16;
 for(unsigned int existing=0;existing<2;existing++) {
  present=existing;port=(struct rtl_fe_port){0};
  assert(!rtl_fe_add_wifi_port(&fe,&dev,1));
  rtl_fe_bridge_key(key,src,dst,0x800,18,port.llid,58);
  rtl_fe_key_set(key,RTL_FE_KEY_TABLE_ID,0);rtl_fe_key_set(key,RTL_FE_KEY_MDATA_BYTE,port.wifi_id);
  for(unsigned int i=0;i<23;i++)if(key[i]!=captured[i]) {
   fprintf(stderr,"registration=%s key[%u]=%08x hardware=%08x\n",existing?"existing":"new",i,key[i],captured[i]);return 1;
  }
  rtl_fe_key_crc(key,&crc,&crc16);
  assert(crc==0xe154a600 && crc16==0x5a9c);
 }
 puts("PASS: new/existing Wi-Fi registration matches captured hardware key and CRC");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-fe-identity-') as directory:
    work = Path(directory)
    (work / 'identity.c').write_text(harness)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(driver),
                    str(work / 'identity.c'), '-o', str(work / 'identity')], check=True)
    subprocess.run([str(work / 'identity')], check=True)
