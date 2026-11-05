#!/usr/bin/env python3
"""Compare normal EDCCA/collision writes with this board's factory ARM64."""
import ctypes
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

registers = {}


def boundary(name, args):
    if name == 'printk':
        return 0
    _, address, mask, value = args
    shift = (mask & -mask).bit_length() - 1
    if name == 'halbb_get_reg':
        return (registers.get(address, 0) & mask) >> shift
    assert name == 'halbb_set_reg', name
    registers[address] = (registers.get(address, 0) & ~mask) | ((value << shift) & mask)
    return 0


oracle = ModuleOracle(root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko',
    ['halbb_cr_cfg_edcca_init', 'halbb_edcca_init', 'halbb_edcca_thre_calc',
     'halbb_edcca_thre_transfer_rssi', 'halbb_set_edcca_thre',
     'halbb_set_collision_th', 'halbb_set_collision_thre'], boundary)
bb, hal, phl = 0x6000000, 0x6020000, 0x6040000
oracle.uc.mem_map(bb, 0x80000)
text = (driver / 'bb-dm.h').read_text()
function = text[text.index('static inline void rtl_ax_bb_dm_edcca_update('):text.rindex('#endif')]
source = r'''
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;typedef int8_t s8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#include "power.h"
struct rtl_ax_bb_dm {u8 edcca_mode,edcca_high,edcca_low;};
static u32 registers[0x20000/4];
static u32 read32(void *ctx,unsigned reg){return registers[reg/4];}
static void write32(void *ctx,unsigned reg,u32 value){registers[reg/4]=value;}
static struct rtl_ax_io io={.read32=read32,.write32=write32};
''' + function + r'''
void native_set(unsigned reg,u32 value){registers[(reg|0x10000)/4]=value;}
u32 native_get(unsigned reg){return registers[(reg|0x10000)/4];}
void native_update(bool linked,u8 rssi){
 struct rtl_ax_bb_dm dm={};rtl_ax_bb_dm_edcca_update(&io,&dm,linked,rssi);
}
'''
addresses = [0x4840, 0x4848, 0xc70, 0xc74, 0xc78, 0xc7c]
rng = random.Random(9617)
cases = 0
with tempfile.TemporaryDirectory(prefix='edcca-runtime-') as folder:
    p = Path(folder)
    (p / 'test.c').write_text(source)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-shared', '-fPIC', '-I' + str(driver), str(p / 'test.c'),
                    '-o', str(p / 'test.so')], check=True)
    native = ctypes.CDLL(str(p / 'test.so'))
    native.native_get.restype = ctypes.c_uint32
    for chip in (8, 32):
        oracle.word(bb, phl)
        oracle.word(bb + 8, hal)
        oracle.uc.mem_write(bb + 0xc14, struct.pack('<I', chip))
        oracle.uc.mem_write(bb + 0xc1c, struct.pack('<I', 2))
        oracle.call('halbb_cr_cfg_edcca_init', bb)
        oracle.call('halbb_edcca_init', bb)
        for linked in (False, True):
            for rssi in range(256):
                for _ in range(3):
                    for address in addresses:
                        value = rng.getrandbits(32)
                        registers[address] = value
                        native.native_set(address, value)
                    oracle.uc.mem_write(bb + 0x424c, bytes([linked]))
                    oracle.uc.mem_write(bb + 0x4290, bytes([rssi]))
                    oracle.call('halbb_edcca_thre_calc', bb)
                    oracle.call('halbb_set_collision_th', bb)
                    native.native_update(linked, rssi)
                    for address in addresses:
                        assert native.native_get(address) == registers[address], (chip, linked, rssi, hex(address))
                    cases += 1
print(f'PASS: {cases} factory ARM64 normal EDCCA/collision vectors on C/XB, including preservation of unrelated register bits')
