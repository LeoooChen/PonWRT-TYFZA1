#!/usr/bin/env python3
"""Compare RFE54 FEM GPIO setup with the board's original ARM64 functions."""
import ctypes
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
assert hashlib.sha256(module.read_bytes()).hexdigest() == (
    '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e')
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
header = (driver / 'bb-dm.h').read_text()
function = header[header.index('static inline void rtl_ax_bb_dm_gpio_start('):
                  header.index('static inline s8 rtl_ax_bb_gain_nibble(')]
source = '''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "power.h"
struct rtl_ax_bb_dm { bool gpio_ready; };
static unsigned int registers[0x20000 / 4];
static unsigned int read32(void *ctx, unsigned int reg) { return registers[reg / 4]; }
static void write32(void *ctx, unsigned int reg, unsigned int v) { registers[reg / 4] = v; }
''' + function + '''
void native(unsigned int chip, unsigned int initial) {
    for (unsigned int i = 0; i < sizeof(registers) / sizeof(*registers); i++) registers[i] = initial;
    struct rtl_ax_io io = {.read32 = read32, .write32 = write32};
    struct rtl_ax_bb_dm dm = {};
    rtl_ax_bb_dm_gpio_start(&io, chip, &dm);
}
unsigned int result(unsigned int reg) { return registers[(reg | 0x10000) / 4]; }
'''
registers = {}
initial = 0


def callback(name, args):
    if name == 'printk':
        return 0
    assert name == 'halbb_set_reg', (name, args)
    _, address, mask, value = args
    shift = (mask & -mask).bit_length() - 1
    registers[address] = ((registers.get(address, initial) & ~mask) |
                          ((value << shift) & mask))
    return 0


names = [f'halbb_gpio_{part}_{chip}' for chip in ('8852c', '8192xb')
         for part in ('setting_init', 'trsw_table', 'rfm')]
oracle = ModuleOracle(module, names, callback)
bb, phl = 0x6000000, 0x6010000
for address in (bb, phl):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(bb, phl)
oracle.uc.mem_write(phl + 0x58fa, b'\x36')
with tempfile.TemporaryDirectory(prefix='tyfza1-gpio-contract-') as temp:
    c, library = Path(temp) / 'native.c', Path(temp) / 'native.so'
    c.write_text(source)
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Werror',
                    '-I', str(driver), str(c), '-o', str(library)], check=True)
    native = ctypes.CDLL(str(library))
    native.result.restype = ctypes.c_uint32
    for chip, native_chip, ic, rfm in [('8852c', 0x52, 8, 0x01080604),
                                      ('8192xb', 0x53, 32, 0x030a0604)]:
        oracle.uc.mem_write(bb + 3092, struct.pack('<I', ic))
        for initial in (0, 0xa5a5a5a5):
            registers.clear()
            oracle.call(f'halbb_gpio_setting_init_{chip}', bb)
            native.native(native_chip, ctypes.c_uint32(initial))
            for address, expected in registers.items():
                actual = native.result(address)
                assert actual == expected, (chip, hex(address), hex(actual), hex(expected))
            if not initial:
                assert registers[0x5894] == registers[0x7894] == rfm
        print(f'{chip} RFE54 GPIO matches original ARM64; RFM={rfm:08x}')
