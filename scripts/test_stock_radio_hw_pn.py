#!/usr/bin/env python3
"""Execute the stock C/XB PN readback chain with synthetic DCTL SRAM contents."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko'
names = ['mac_sram_dbg_read', 'refresh_security_cam_info_8852c',
         'refresh_security_cam_info_8192xb']
adapter, platform, hw, ops, io, table, entries, info = [
    0x6000000 + i * 0x10000 for i in range(8)]
reads, writes, locks = [], [], []
selected, pn, macid = 0, 0, 0


def callback(name, args):
    global selected
    if name == 'write32':
        assert args[0] == adapter and args[1] == 0xc04
        selected = args[2]
        writes.append(selected)
        return 0
    if name == 'read32':
        assert args[0] == adapter and args[1] == 0x40000
        offset = selected - 0x18800000 - macid * 32
        assert offset in range(0, 32, 4)
        reads.append(selected)
        return {0: (pn & 65535) << 16, 4: pn >> 16}.get(offset, 0)
    if name in ('lock', 'unlock'):
        assert args[1] == hw + 0x108
        locks.append(name)
        return 0
    if name == 'message':
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, names, callback)
for address in (adapter, platform, hw, ops, io, table, entries, info):
    oracle.uc.mem_map(address, 65536)
oracle.word(adapter, ops)
oracle.word(ops, io)
oracle.word(adapter + 24, platform)
oracle.word(adapter + 88, hw)
oracle.word(hw + 96, table)
for offset, name in ((160, 'lock'), (168, 'unlock'), (176, 'message')):
    oracle.word(platform + offset, oracle.callback(name))
for offset, name in ((32, 'read32'), (40, 'write32')):
    oracle.word(io + offset, oracle.callback(name))
for index in range(128):
    oracle.word(table + index * 8, entries + index * 32)

checks = 0
for chip in ('8852c', '8192xb'):
    for macid in range(128):
        for pn in (0, 1, 0xffff, 0x123456abcdef):
            reads.clear()
            writes.clear()
            locks.clear()
            oracle.uc.mem_write(info, bytes(32))
            oracle.uc.mem_write(info + 8, bytes([macid]))
            assert oracle.call('refresh_security_cam_info_' + chip, adapter, info) == 0
            assert int.from_bytes(oracle.uc.mem_read(info, 6), 'little') == pn
            expected = [0x18800000 + macid * 32 + i * 4 for i in range(8)]
            assert reads == writes == expected
            assert locks == ['lock', 'unlock'] * 8
            assert oracle.unpack(hw + 168, 'I')[0] == 0
            checks += 1

source = (root / 'artifacts/live-radio-pe-ni-5g/security.h').read_text()
start = source.index('static inline u64 rtl_ax_security_read_pn(')
end = source.index('\n}\n', start) + 3
native = r'''
#include <assert.h>
#include <stdint.h>
typedef uint32_t u32;
typedef uint64_t u64;
struct rtl_ax_io {void *ctx; void (*write32)(void *,unsigned,unsigned);
 unsigned (*read32)(void *,unsigned);};
static unsigned selected, writes, reads, macid;
static u64 pn;
static void write32(void *ctx,unsigned reg,unsigned value) {
 (void)ctx;assert(reg==0xc04 && value==0x18800000+macid*32+writes*4);
 selected=value;writes++;
}
static unsigned read32(void *ctx,unsigned reg) {
 (void)ctx;assert(reg==0x40000 && reads+1==writes);
 assert(selected==0x18800000+macid*32+reads*4);
 return reads++ ? pn>>16 : (pn&65535)<<16;
}
'''
native += source[start:end] + r'''
int main(void) {
 const u64 vectors[]={0,1,65535,0x123456abcdefULL};
 struct rtl_ax_io io={.write32=write32,.read32=read32};
 for(macid=0;macid<128;macid++)for(unsigned i=0;i<4;i++) {
  pn=vectors[i];writes=reads=0;
  assert(rtl_ax_security_read_pn(&io,macid)==pn);
  assert(writes==2 && reads==2);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-hw-pn-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(native)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)

result = {
    'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
    'functions': names,
    'cases': checks,
    'native_readback_cases': 512,
    'MACIDs': 128,
    'SRAM_selector': 13,
    'SRAM_base': '0x18800000',
    'MACID_stride_bytes': 32,
    'address_register': '0xc04',
    'data_register': '0x40000',
    'PN_layout': 'DCTL[0] high16 is PN low16; DCTL[1] is PN high32',
    'scope': 'Actual stock host instructions; MMIO returns synthetic PN, not a live radio read',
}
(root / 'logs/stock-radio-hw-pn-contract.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
