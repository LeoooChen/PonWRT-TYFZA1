#!/usr/bin/env python3
"""Run production FE admission/restore with five different LAN register states."""
import hashlib
import json
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision', help='Run the same hardware contract against an older commit')
args = parser.parse_args()
relative = str((drivers / 'rtl9617c-fe.c').relative_to(root))
source = subprocess.check_output(['git', 'show', args.revision + ':' + relative], text=True) if args.revision else (drivers / 'rtl9617c-fe.c').read_text()

def function(name):
    start = re.search(r'^(?:static )?int ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

registers = re.search(r'static const unsigned int fe_registers\[\] = \{.*?\};', source, re.S).group()
common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <linux/ppp_defs.h>
#include <linux/if_ether.h>
#include "rtl9617c-fe-pipeline.h"
#include "rtl9617c-wifi-format.h"
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define FE_LAN_PORTS 5
#define FE_LAN_RX_CTRL(p) (0xa5f8+0x90*(p))
#define lower_32_bits(v) ((unsigned int)(v))
#define upper_32_bits(v) ((unsigned int)((uint64_t)(v)>>32))
struct mutex {int unused;};
struct list_head {int unused;};
struct rtl_fe_flow {int unused;};
#define list_for_each_entry_safe(p,n,h,m) for(p=NULL,n=NULL;p;p=n)
static void mutex_lock(struct mutex *m){(void)m;}
static void mutex_unlock(struct mutex *m){(void)m;}
struct rtl_fe {
 struct mutex lock;struct list_head flows;
 uint64_t dma;bool prepared,enabled;int error;
 unsigned int saved[64],lan_saved[5],mask_saved[8],classifier_saved[2][7],map_saved[2][4];
 unsigned int wifi_ippb_saved,wifi_ippb,wifi_mask_saved[8],wifi_mask[8];
 unsigned int bridge_mask_saved[6][8],bridge_mask[6][8];
 unsigned int reg[0x10000/4],mask[8],cls[2][7],map[256];
 struct rtl_fe_cam_protocol protocol_cam[3];unsigned char ppp_protocol_ids[2];
 unsigned int cam[9][64][5];
 unsigned int admission_writes,cache_ready,fail_mask;
};
static unsigned int fe_read(void *ctx,unsigned int reg){struct rtl_fe *f=ctx;assert(reg<0x10000);return f->reg[reg/4];}
static void fe_write(void *ctx,unsigned int reg,unsigned int value){
 struct rtl_fe *f=ctx;assert(reg<0x10000);
 if(reg==0x38c4 && value==0xa0000000)f->cache_ready=1;
 if(reg>=0xa5f8 && reg<=0xa838 && !((reg-0xa5f8)%0x90) && ((value>>16)&63)==25){
  /* Admission follows table bases, cache setup and five output selectors. */
  assert(f->prepared && f->cache_ready && f->reg[0x383c/4]==0x03800000);
  assert((f->reg[0xa1e0/4]&1023)==1023 && (f->reg[0x3400/4]&BIT(19)));
  assert(!(f->cls[0][6]&BIT(16)));f->admission_writes++;
 }
 f->reg[reg/4]=value;
}
static int fe_access(void *ctx,unsigned int reg,unsigned int value){
 struct rtl_fe *f=ctx;unsigned int index=value&255,i;
 if(reg==0x11dc){
  assert(index==RTL_WIFI_SOURCE);
  if(value&BIT(30))f->wifi_ippb=f->reg[0x11e0/4];else f->reg[0x11e0/4]=f->wifi_ippb;
 } else if(reg==0x3200){
  unsigned int bank=(value>>16)&15;index=value&63;assert(bank==2||bank==8);
  for(i=0;i<5;i++)if(value&BIT(30))f->cam[bank][index][i]=f->reg[(0x3214-i*4)/4];
  else f->reg[(0x3214-i*4)/4]=f->cam[bank][index][i];
 } else if(reg==0x3910){
  if(f->fail_mask){f->fail_mask=0;return -ETIMEDOUT;}
  unsigned int bank=(value>>6)&1;
  unsigned int slot=value&63;
  unsigned int *selected=slot==63?f->mask:slot==62?f->wifi_mask:f->bridge_mask[61-slot];
  for(i=0;i<4;i++)if(value&BIT(30))selected[bank*4+i]=f->reg[(0x3920-i*4)/4];
  else f->reg[(0x3920-i*4)/4]=selected[bank*4+i];
 } else if(reg==0x33b0){
  unsigned int side=value&1;assert((value&0x3fff)==1024+side);
  for(i=0;i<7;i++)if(value&BIT(30))f->cls[side][i]=f->reg[(0x33cc-i*4)/4];
  else f->reg[(0x33cc-i*4)/4]=f->cls[side][i];
 } else if(reg==0x1654){
  if(value&BIT(30))f->map[index]=f->reg[0x1658/4];else f->reg[0x1658/4]=f->map[index];
 } else {
  assert(reg==0x393c && value==1);
  /* A table clear must never occur while any LAN still admits FE traffic. */
  for(i=0;i<5;i++)assert(fe_read(f,0xa5f8+i*0x90)==f->lan_saved[i]);
 }
 return 0;
}
static struct rtl_dq_io fe_io(struct rtl_fe *f){return (struct rtl_dq_io){f,fe_read,fe_write,fe_access};}
static void fe_collect_ports(struct rtl_fe *f){(void)f;}
static int fe_delete(struct rtl_fe *f,struct rtl_fe_flow *p){(void)f;(void)p;assert(0);return 0;}
static void usleep_range(unsigned int a,unsigned int b){assert(a==1000 && b==2000);}
'''
lan_defines = re.search(r'^#define FE_LAN_PORTS[^\n]*\n#define FE_LAN_RX_CTRL[^\n]*', source, re.M)
if lan_defines:
    common = common.replace('#define FE_LAN_PORTS 5\n#define FE_LAN_RX_CTRL(p) (0xa5f8+0x90*(p))', lan_defines.group())
test = r'''
int main(void){
 struct rtl_fe f={.dma=0x03800000,.wifi_ippb=0xa5000004};unsigned int lan[5],old_regs[64],old_mask[8],old_cls[2][7],old_map[256],old_demux,old_wifi_mask[8];
 unsigned int old_bridge_mask[6][8];
 for(unsigned int i=0;i<0x10000/4;i++)f.reg[i]=0x80000000U+i;
 for(unsigned int i=0;i<5;i++)f.reg[(0xa5f8+i*0x90)/4]=lan[i]=0x18100400U|(i<<24);
 for(unsigned int i=0;i<8;i++){f.mask[i]=old_mask[i]=0x55000000+i;f.wifi_mask[i]=old_wifi_mask[i]=0xaa550000+i;}
 for(unsigned int s=0;s<6;s++)for(unsigned int i=0;i<8;i++)
  f.bridge_mask[s][i]=old_bridge_mask[s][i]=0x5a000000+s*8+i;
 for(unsigned int s=0;s<2;s++)for(unsigned int i=0;i<7;i++)f.cls[s][i]=old_cls[s][i]=0x11000000+s*7+i;
 for(unsigned int i=0;i<256;i++)f.map[i]=old_map[i]=0x100+i;
 for(unsigned int i=0;i<ARRAY_SIZE(fe_registers);i++)old_regs[i]=fe_read(&f,fe_registers[i]);
 old_demux=fe_read(&f,0xa1e0);
 for(unsigned int cycle=0;cycle<3;cycle++){
  assert(!rtl_fe_enable(&f) && f.enabled && f.prepared);
  assert(f.wifi_ippb==0xa5000012);
  assert(fe_read(&f,0x3700)==8 && fe_read(&f,0x3704)==63 && fe_read(&f,0x3708)==318);
  assert(fe_read(&f,0x370c)==(61|2U<<8) && fe_read(&f,0x3710)==(60|3U<<8));
  assert(fe_read(&f,0x371c)==(57|6U<<8) && fe_read(&f,0x3720)==(56|7U<<8));
  assert(f.ppp_protocol_ids[0]==1 && f.ppp_protocol_ids[1]==2);
  assert(f.cam[2][0][0]==0x18864 && f.cam[8][0][0]==0x10021 && f.cam[8][1][0]==0x10057);
  for(unsigned int b=0;b<256;b++)assert(((f.wifi_mask[b/32]>>(b%32))&1)==((b>=135 && b<143)?0:((f.mask[b/32]>>(b%32))&1)));
  for(unsigned int i=0;i<5;i++)assert(fe_read(&f,0xa5f8+i*0x90)==((lan[i]&~(63U<<16))|(25U<<16)));
  assert(!(f.cls[0][6]&BIT(16)) && (f.cls[1][6]&BIT(16)) && ((f.cls[1][6]>>17)&63)==7);
  assert(fe_read(&f,0xa1e0)==(old_demux|0x3ff));
  unsigned int writes=f.admission_writes;assert(!rtl_fe_enable(&f) && f.admission_writes==writes);
  assert(!rtl_fe_disable(&f) && !f.enabled && !f.prepared);
  assert(f.wifi_ippb==0xa5000004);
  for(unsigned int i=0;i<5;i++)assert(fe_read(&f,0xa5f8+i*0x90)==lan[i]);
  for(unsigned int i=0;i<ARRAY_SIZE(fe_registers);i++)assert(fe_read(&f,fe_registers[i])==old_regs[i]);
  assert(!memcmp(f.mask,old_mask,sizeof(old_mask)) && !memcmp(f.cls,old_cls,sizeof(old_cls)));
  assert(!memcmp(f.wifi_mask,old_wifi_mask,sizeof(old_wifi_mask)));
  assert(!memcmp(f.bridge_mask,old_bridge_mask,sizeof(old_bridge_mask)));
  assert(!memcmp(f.map,old_map,sizeof(old_map)));
  assert(!rtl_fe_disable(&f));
  assert(!f.cam[2][0][0] && !f.cam[8][0][0] && !f.cam[8][1][0]);
 }
 assert(f.admission_writes==15);
 f.fail_mask=1;assert(rtl_fe_enable(&f)==-ETIMEDOUT && !f.enabled && !f.prepared);
 for(unsigned int i=0;i<5;i++)assert(fe_read(&f,0xa5f8+i*0x90)==lan[i]);
 /* Warm parser state can place the PPP protocols at arbitrary CAM rows. */
 f.cam[2][11][0]=0x18864;f.cam[8][5][0]=0x10021;f.cam[8][9][0]=0x10057;
 assert(!rtl_fe_enable(&f));
 assert(f.ppp_protocol_ids[0]==6 && f.ppp_protocol_ids[1]==10);
 for(unsigned int i=0;i<3;i++)assert(!f.protocol_cam[i].created);
 assert(!rtl_fe_disable(&f));
 assert(f.cam[2][11][0]==0x18864 && f.cam[8][5][0]==0x10021 && f.cam[8][9][0]==0x10057);
 puts("Five-LAN FE:ordered admission,physical source preservation,direct L2TM output,three exact restore cycles and failed preparation passed");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='fe-lan-lifecycle-') as work:
    path = Path(work) / 'test.c'
    binary = Path(work) / 'test'
    path.write_text(common + registers + '\n' + '\n'.join(function(n) for n in
        ['fe_prepare', 'fe_restore', 'rtl_fe_enable', 'rtl_fe_disable']) + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined',
        '-I', str(drivers), str(path), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/fe-five-lan-lifecycle.json').write_text(json.dumps({
    'production_source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'LAN_ports': [0, 1, 2, 3, 4], 'exact_enable_disable_cycles': 3,
    'failed_prepare_admits_no_LAN': True, 'physical_LSPID_preserved': True,
    'post_PE_LAN_output': 'NI demux3 to L2TM; upper selectors preserved',
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified_by_this_test': False,
}, indent=2) + '\n')
