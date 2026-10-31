#!/usr/bin/env python3
"""Compare scan RPL extraction with the board's original ARM64 PHY parser."""
import ctypes
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'


def boundary(name, args):
    if name == 'halbb_mod_rssi_by_path_en':
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['halbb_physts_ie_00', 'halbb_physts_ie_01',
                              'halbb_cnvrt_2_sign'], boundary)
bb, ie = 0x6000000, 0x6010000
oracle.uc.mem_map(bb, 0x10000)
oracle.uc.mem_map(ie, 0x10000)
source = r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8; typedef uint32_t u32;
#define BIT(n) (1U << (n))
static u32 get_unaligned_le32(const void *p) { u32 v; memcpy(&v,p,4); return v; }
#include "scan.h"
int rpl(const u8 *p,unsigned n) { return rtl_ax_scan_rpl(p,n); }
'''
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
with tempfile.TemporaryDirectory(prefix='scan-rpl-') as tmp:
    work = Path(tmp)
    (work / 'test.c').write_text(source)
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror', '-I' + str(driver),
                    str(work / 'test.c'), '-o', str(work / 'test.so')], check=True)
    native = ctypes.CDLL(str(work / 'test.so')).rpl
    native.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    cases = 0
    for kind in (0, 1):
        for value in range(256):
            for low in (0, 1):
                payload = bytes([kind | low << 7, value]) + bytes(14 if kind == 0 else 30)
                oracle.uc.mem_write(bb, bytes(0x10000))
                oracle.uc.mem_write(ie, payload)
                assert oracle.call(f'halbb_physts_ie_0{kind}', bb, ie) == 1
                original = oracle.unpack(bb + 0x3339, 'B')[0]
                for users in (0, 1, 8):
                    # User MACID-valid bits are deliberately absent for neighbour APs.
                    for counters in (0, 1):
                        header = struct.pack('<II', users | counters << 29, 2 << 16)
                        header += bytes(((users + 1) & ~1) * 4 + counters * 96 + 16)
                        phy = bytes([0x8b + kind, (8 + len(payload)) // 8]) + bytes(6) + payload
                        frame = header + phy
                        assert native(frame, len(frame)) == original == value
                        cases += 1
                        assert native(frame, len(frame) - 1) < 0
    print(f'Scan RPL: {cases} CCK/OFDM vectors match original ARM64; unassociated users and truncation passed')
