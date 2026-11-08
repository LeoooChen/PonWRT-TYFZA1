#!/usr/bin/env python3
"""Compare the native AP HIQ stop command with target h2c_pkt_drop."""
from pathlib import Path
import argparse,struct,subprocess,tempfile,sys,re
r=Path(__file__).resolve().parents[1];d=r/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--wifi',type=Path,required=True);a=p.parse_args()
sys.path.insert(0,str(r/'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4,UC_ARM64_REG_X5,UC_ARM64_REG_X6,UC_ARM64_REG_X7
size=0;header=[]
packet,payload=0x6000000,0x6010000

def cb(name,args):
 global size,header
 if name in ('printk','h2cb_free','h2c_pkt_build_txd','platform_TX','h2c_end_flow'):return 0
 if name=='platform_memset':oracle.uc.mem_write(args[1],bytes([args[2]&255])*args[3]);return 0
 if name=='h2cb_alloc':return packet
 if name=='h2cb_put':size=args[1];oracle.uc.mem_write(payload,bytes(512));return payload
 if name=='h2c_agg_enqueue':return 1
 if name=='h2c_pkt_set_hdr':
  header=args[2:4]+[oracle.uc.reg_read(reg) for reg in (UC_ARM64_REG_X4,UC_ARM64_REG_X5,UC_ARM64_REG_X6,UC_ARM64_REG_X7)];return 0
 raise AssertionError((name,args))
oracle=ModuleOracle(a.wifi,['h2c_pkt_drop'],cb)
for addr in (packet,payload):oracle.uc.mem_map(addr,0x10000)
adapter,info=oracle.scratch,oracle.scratch+0x1000
oracle.uc.mem_write(adapter+33,b'\4')
ops=oracle.scratch+0x2000
oracle.word(adapter+24,ops);oracle.word(ops+96,oracle.callback('platform_memset'));oracle.word(ops+48,oracle.callback('platform_TX'))
# Drop selector8, MBID0, band0/port0. No receive/done acknowledgement.
oracle.uc.mem_write(info,bytes([8])+bytes(15))
assert oracle.call('h2c_pkt_drop',adapter,info,0,0)==0
expected=bytes(oracle.uc.mem_read(payload,size));assert size==24 and header==[0,1,9,27,0,0],header
s=(d/'pci.c').read_text()
def fn(name):
 start=re.search(r'^(?:static )?(?:int) '+name+r'\([^;]*?\)\s*\{',s,re.M).start();end=s.index('{',start)+1;depth=1
 while depth:depth+=(s[end]=='{')-(s[end]=='}');end+=1
 return s[start:end]+'\n'
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
#define RTL_AX_H2C_CMD 0
struct rtl_ax_ap {int unused;};struct pci_dev {struct rtl_ax_ap *radio;};
#define pci_get_drvdata(p) ((p)->radio)
static unsigned calls;static int failure;static u8 captured[24];
static void rtl_ax_store32(void *p,unsigned v){memcpy(p,&v,4);}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned cls,unsigned cat,unsigned c,unsigned f,const void *data,unsigned bytes,bool ack){
 assert(r&&!cls&&cat==1&&c==9&&f==27&&bytes==24&&!ack);calls++;memcpy(captured,data,24);return failure;}
'''+fn('rtl_ax_port0_drop_mbid')+fn('rtl_ax_mac_drop_bcmc')+r'''
int main(void){struct rtl_ax_ap radio={};struct pci_dev dev={&radio};
 assert(!rtl_ax_mac_drop_bcmc(&dev)&&calls==1);assert(fwrite(captured,1,24,stdout)==24);
 failure=-5;assert(rtl_ax_mac_drop_bcmc(&dev)==-5&&calls==2);
}
'''
with tempfile.TemporaryDirectory() as tmp:
 t=Path(tmp);(t/'t.c').write_text(code)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(t/'t.c'),'-o',str(t/'t')],check=True)
 assert subprocess.check_output([str(t/'t')])==expected
print('AP HIQ stop: native command exactly matches original MBID0 selector8, flags/length, and transport errors propagate')
