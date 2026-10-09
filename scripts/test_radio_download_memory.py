#!/usr/bin/env python3
"""Compare the native DLFW preparation with this board's ARM64 functions/tables."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/download.h'
operations = ('dmac_func_pre_en', 'dle_func_en', 'dle_clk_en', 'dle_mix_cfg',
              'wde_quota_cfg', 'ple_quota_cfg', 'set_fc_func_en', 'set_fc_h2c')
names = [f'{op}_{chip}' for chip in ('8852c', '8192xb') for op in operations] + ['hfc_reset_param']
registers, trace, chip_id = bytearray(65536), [], 2

def read(reg, width=4):
    return int.from_bytes(registers[reg:reg + width], 'little')

def write(reg, value, width=4):
    value &= (1 << (width * 8)) - 1
    registers[reg:reg + width] = value.to_bytes(width, 'little')
    trace.append((width, reg, value))

def handler(name, args):
    if name.startswith('read'):
        return read(args[1], int(name[4:]) // 8)
    if name.startswith('write'):
        write(args[1], args[2], int(name[5:]) // 8)
    elif name == 'is_chip_id':
        return int(args[1] == chip_id)
    elif name != 'log':
        raise AssertionError(('Unexpected stock callback', name))
    return 0

oracle = ModuleOracle(module, names, handler)
scratch = oracle.scratch
oracle.word(scratch, scratch + 0x1000)
oracle.word(scratch + 0x1000, scratch + 0x2000)
oracle.word(scratch + 24, scratch + 0x3000)
oracle.word(scratch + 88, scratch + 0x4000)
oracle.uc.mem_write(scratch + 0x4000 + 8, struct.pack('<I', 2))  # PCIe interface
for index, name in enumerate(('read8', 'write8', 'read16', 'write16', 'read32', 'write32')):
    oracle.word(scratch + 0x2000 + index * 8, oracle.callback(name))
oracle.word(scratch + 0x3000 + 176, oracle.callback('log'))
oracle.word(scratch + 464, scratch + 0x5000)
for offset, target in ((8, 0x5100), (16, 0x5200), (24, 0x5300), (32, 0x5400), (40, 0x5500)):
    oracle.word(scratch + 0x5000 + offset, scratch + target)

R8, R16, R32 = [C.CFUNCTYPE(t, C.c_void_p, C.c_uint) for t in (C.c_ubyte, C.c_ushort, C.c_uint)]
W8, W16, W32 = [C.CFUNCTYPE(None, C.c_void_p, C.c_uint, t) for t in (C.c_ubyte, C.c_ushort, C.c_uint)]
DELAY = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)
POLL = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.POINTER(C.c_uint))
class IO(C.Structure):
    _fields_ = [('ctx', C.c_void_p)] + list(zip(('r8', 'r16', 'r32', 'w8', 'w16', 'w32', 'delay', 'poll'),
                                             (R8, R16, R32, W8, W16, W32, DELAY, POLL)))
def poll(ctx, reg, mask, expected, delay, timeout, last):
    last[0] = read(reg)
    assert (last[0] & mask) == expected
    return 0
callbacks = [R8(lambda c, r: read(r, 1)), R16(lambda c, r: read(r, 2)), R32(lambda c, r: read(r)),
             W8(lambda c, r, v: write(r, v, 1)), W16(lambda c, r, v: write(r, v, 2)),
             W32(lambda c, r, v: write(r, v)), DELAY(lambda c, us: None), POLL(poll)]
io = IO(None, *callbacks)

tables = {}
for family in ('8852c', '8192xb'):
    address, size = oracle.symbols['dle_mem_pcie_' + family]
    entries = []
    for offset in range(0, size, 56):
        mode = oracle.unpack(address + offset, 'I')[0]
        pointers = oracle.unpack(address + offset + 8, '6Q')
        if mode == 5:  # Original MAC_AX_QTA_DLFW enum.
            tables[family] = [list(oracle.unpack(p, fmt)) for p, fmt in
                              zip(pointers, ('3H', '3H', '5H', '5H', '13H', '13H'))]
            assert tables[family] == [[64, 0, 2048], [128, 2544, 16], [0] * 5, [0] * 5,
                                      [0, 0, 16, 256] + [0] * 9,
                                      [0, 0, 32, 256] + [0] * 9]
    assert family in tables

with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-download-memory-') as directory:
    work = Path(directory)
    source, binary = work / 'native.c', work / 'native.so'
    source.write_text('''#include "download.h"
void pre(const struct rtl_ax_io *io){rtl_ax_dmac_pre_enable(io);}
int dle(const struct rtl_ax_io *io,unsigned int min){return rtl_ax_dle_download(io,min);}
void hfc(const struct rtl_ax_io *io,unsigned int prec,unsigned int full){rtl_ax_hfc_download(io,prec,full);}
''')
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O2', '-shared', '-fPIC',
                    '-I' + str(header.parent), str(source), '-o', str(binary)], check=True)
    native = C.CDLL(str(binary))
    native.pre.argtypes = [C.POINTER(IO)]
    native.dle.argtypes = [C.POINTER(IO), C.c_uint]
    native.hfc.argtypes = [C.POINTER(IO), C.c_uint, C.c_uint]
    rng, vectors, retained = random.Random(0x9617c), 0, []
    for family, chip_id in (('8852c', 2), ('8192xb', 3)):
        # Actual hfc_reset_param(DLFW) retains all old precost fields, even
        # with nonzero values. Neither cold zero nor operational 40 becomes 256.
        for precost in (0, 40, 256):
            state = struct.pack('<4H4B', 2, precost, 48, 48, 1, 0, 0, 0)
            oracle.uc.mem_write(scratch + 0x5500, state)
            oracle.uc.mem_write(scratch + 472, struct.pack('<I', 5))
            assert not oracle.call('hfc_reset_param', scratch)
            assert bytes(oracle.uc.mem_read(scratch + 0x5500, len(state))) == state
            retained.append({'chip': family, 'H2C_precost': precost, 'retained': True})
        for vector in range(128):
            initial = rng.randbytes(65536)
            registers[:] = initial;trace.clear()
            assert not oracle.call('dmac_func_pre_en_' + family, scratch)
            expected = list(trace);final = bytes(registers)
            registers[:] = initial;trace.clear();native.pre(C.byref(io))
            assert trace == expected and bytes(registers) == final
            vectors += 1
            # Use the original table objects and change only the same WCPU
            # minimum that dle_init copies from the operational profile.
            cfg = scratch + 0x6000
            for offset, name in zip((8, 16, 24, 32, 40, 48),
                                    ('wde_size18', 'ple_size18', 'wde_qt17', 'wde_qt17', 'ple_qt44', 'ple_qt45')):
                target = oracle.symbols[name][0]
                if name == 'wde_qt17':
                    target = scratch + (0x6100 if offset == 24 else 0x6200)
                    oracle.uc.mem_write(target, struct.pack('<5H', 0, 60 if offset == 24 else 0, 0, 0, 0))
                oracle.word(cfg + offset, target)
            registers[:] = initial;trace.clear()
            for operation, args in [('dle_func_en', (0,)), ('dle_clk_en', (1,)), ('dle_mix_cfg', (cfg,)),
                                    ('wde_quota_cfg', (scratch + 0x6100, scratch + 0x6200)),
                                    ('ple_quota_cfg', (oracle.symbols['ple_qt44'][0], oracle.symbols['ple_qt45'][0])),
                                    ('dle_func_en', (1,))]:
                assert not oracle.call(operation + '_' + family, scratch, *args)
            write(0x8d00, 3);write(0x9100, 3)
            expected = list(trace[:-2]);final = bytes(registers)
            registers[:] = initial;write(0x8d00, 3);write(0x9100, 3);trace.clear()
            assert not native.dle(C.byref(io), 60)
            assert trace == expected and bytes(registers) == final, family
            vectors += 1
            precost, condition = (0, 40, 256)[vector % 3], vector % 4
            oracle.uc.mem_write(scratch + 0x5500, struct.pack('<4H4B', 0, precost, 0, 0, 0, condition, 0, 0))
            registers[:] = initial;trace.clear()
            for operation, args in [('set_fc_func_en', (0, 0)), ('set_fc_h2c', ()), ('set_fc_func_en', (0, 1))]:
                assert not oracle.call(operation + '_' + family, scratch, *args)
            expected = list(trace);final = bytes(registers)
            registers[:] = initial;trace.clear();native.hfc(C.byref(io), precost, condition)
            assert trace == expected and bytes(registers) == final
            vectors += 1
report = {'stock_module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'native_download_header_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
          'stock_PCIe_DLFW_tables': tables, 'stock_machine_code_vectors': vectors,
          'WCPU_minimum_from_operational_profile': 60, 'HFC_precost_retention': retained,
          'FIFO_bytes': 458752, 'download_H2C_minimum_and_maximum_pages': 256,
          'native_cold_HFC_precost': 0, 'HW_firmware_download_verified': False}
(root / 'logs/radio-download-memory-test.json').write_text(json.dumps(report, indent=2) + '\n')
print('Native DLFW:768 actual stock DMAC/DLE/HFC register traces match;both PCIe memory tables and six HFC retained-state cases verified')
