#!/usr/bin/env python3
"""Compare native CPU layout/credits with executed TYFZA1 initialization."""
from pathlib import Path
import json
import re
import runpy
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.argv = [str(root / 'tools/trace_stock_qm_contract.py'), '--registers']
factory = runpy.run_path(sys.argv[0])
oracle, ni = factory['oracle'], factory['ni']
tables = factory['tables']
expected = {}
# Apply the same stock threshold setter to the native allocation sizes.
# Profile4 is a private copy for CPU3, not the stock DQ profile2/3.
for profile, stock_profile, count in ((0, 0, 256), (1, 1, 2492), (4, 0, 2492)):
    tables[0x95b0][profile] = list(tables[0x95b0][stock_profile])
    argument = oracle.scratch + 0x1000
    oracle.uc.mem_write(argument, struct.pack('<5H', 0, count * 7 // 8, count * 6 // 8, 0, 0))
    assert oracle.call('aal_l3_te_cb_voq_threshold_profile_set', 0, profile, 6, argument) == 0
    expected[profile] = list(tables[0x95b0][profile])
layout = oracle.unpack(ni + 0x61c8, 'I')[0]
assert layout == 0x18041804
assert layout & 63 == 4 and (layout >> 8) & 63 == 24
assert (layout >> 16) & 63 == 4 and (layout >> 24) & 63 == 24

# Check the original decoder, not just constants inferred from registers.
def boundary(name,args):raise AssertionError((name,args))
o=factory['ModuleOracle'](root/'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',['ca_ni_rx_napi_get_header_from_64bit_epp'],boundary,external_symbols={'memstart_addr':0x5007010,'printk':0x5008000})
o.word(0x5007010,0)
ring=0x7000000;packet=0x7100000;virt=0xffffff8000000000+packet
o.uc.mem_map(ring,4096);o.uc.mem_map(virt,4096)
args=[o.scratch+i*16 for i in range(8)]
flag=o.scratch+0x100
reports=[]
for wifi in (0,1,60):
 o.uc.mem_write(o.scratch,bytes(0x200))
 o.word(args[0],ring);o.uc.mem_write(ring,struct.pack('<II',packet|0x54,wifi<<14|1516))
 o.word(o.scratch+0xff00,flag)
 o.call('ca_ni_rx_napi_get_header_from_64bit_epp',*args)
 assert o.unpack(args[0],'Q')[0]==virt+2040
 assert o.unpack(args[1],'I')[0]==packet|0x54
 if wifi:
  assert o.unpack(args[6],'B')[0]==wifi and o.unpack(args[7],'I')[0]==1500 and o.unpack(flag,'B')[0]==1
 else:
  assert o.unpack(args[2],'Q')[0]==virt+64 and o.unpack(args[3],'Q')[0]==virt+72 and o.unpack(flag,'B')[0]==0
 reports.append({'wifi_id':wifi,'factory_buffer_headroom':64,'factory_skb_pointer_offset':2040,'compact_payload':1500 if wifi else None})

source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c').read_text()
a = source.index('static int rtl_qm_cpu_credits(')
b = source.index('\nint rtl_qm_rx_start(', a)
fixture = r'''
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "rtl9617c-qm-layout.h"
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define RTL_QM_PE_BUFFERS 2496
#define RTL_QM_BUFFERS_PER_EQ 256
#define RTL_QM_PE_EQ 6
#define EQ_ID 4
struct rtl_qm_rx {void *ni;};
static unsigned char memory[65536];
static u32 rows[8][3], selections[256], credits[32];
static void writel(u32 value, void *address) {memcpy(address, &value, 4);}
static u32 readl(void *address) {u32 value;memcpy(&value, address, 4);return value;}
static int rtl_qm_indirect(struct rtl_qm_rx *rx,unsigned reg,u32 request) {
 unsigned index=request&255;assert((request&0xc0000000)==0xc0000000);
 if(reg==0x95b0){assert(index<8);for(unsigned i=0;i<3;i++)rows[index][i]=readl(rx->ni+0x95bc-i*4);}
 else if(reg==0x95c0)selections[index]=readl(rx->ni+0x95c4);
 else {assert(reg==0x95a8);credits[index]=readl(rx->ni+0x95ac);}
 return 0;
}
'''
fixture += source[a:b]
fixture += r'''
int main(void) {
 struct rtl_qm_rx rx={memory};
 assert(RTL_QM_HEADROOM==64 && RTL_QM_TAILROOM==384 && RTL_QM_DATA_BYTES==1600);
 assert(RTL_QM_PACKET_LAYOUT==0x18041804);
 rows[3][0]=0x12345678; /* Existing PON profile must not be overwritten. */
 assert(!rtl_qm_cpu_credits(&rx));assert(rows[3][0]==0x12345678);
 assert(credits[0]==0x41004100 && credits[2]==0x400049bc && credits[3]==0x400049bc);
 for(unsigned cos=0;cos<8;cos++) {
  assert(selections[cos]==0 && selections[16+cos]==1 && selections[24+cos]==4);
 }
 for(unsigned p=0;p<8;p++)if(p==0||p==1||p==4)
  printf("%u %u %u %u\n",p,rows[p][0],rows[p][1],rows[p][2]);
}
'''
with tempfile.TemporaryDirectory(prefix='qm-factory-') as work:
    work = Path(work)
    (work / 'test.c').write_text(fixture)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-I', str(root / 'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    output = subprocess.check_output([str(work / 'test')], text=True)
    actual = {int(row[0]): list(map(int, row[1:])) for line in output.splitlines() if (row := line.split())}
assert actual == expected, (actual, expected)
report = {'factory_module': 'TYFZA1 ca-ne.ko', 'packet_layout': hex(layout),
          'headroom': 64, 'tailroom': 384, 'executed_stock_decoder_cases': reports, 'native_credit_words_match_stock_setter': True,
          'native_VOQ_profiles': actual, 'native_pool_sizes_and_BID_ranges_unchanged': True,
          'PON_profile_preserved': True, 'hardware_validation': False}
out = root / 'artifacts/test-qm-factory-layout'
out.mkdir(parents=True, exist_ok=True)
(out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
