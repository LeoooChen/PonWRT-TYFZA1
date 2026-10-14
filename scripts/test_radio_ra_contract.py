#!/usr/bin/env python3
"""Compare native HT20 RA commands with the original board's ARM64 code."""
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['rtw_halbb_raregistered', 'halbb_init_ra_by_rssi', 'halbb_nss_mapping',
         'halbb_hw_mode_mapping', 'halbb_get_opt_giltf', 'halbb_ax_giltf_chk',
         'halbb_hw_bw_mapping', 'halbb_hw_bw_mode_chk', 'halbb_chk_bw_under_40',
         'halbb_giltf_trans', 'halbb_stbc_mapping', 'halbb_ldpc_chk',
         'halbb_sgi_chk', 'halbb_ac_n_sgi_chk', 'halbb_ramask_mod',
         'halbb_ramask_trans', 'halbb_set_csi_rate', 'halbb_rssi_lv_dec',
         'halbb_ramask_by_rssi', 'halbb_physts_ie_hdr',
         'halbb_physts_fd_rpl_2_rssi_cvrt']
commands = []


def callback(name, args):
    if name == 'printk':
        return 0
    assert name == 'halbb_fill_h2c_cmd'
    assert args[1:4] == [16, 0, 1]
    commands.append(bytes(oracle.uc.mem_read(oracle.uc.reg_read(UC_ARM64_REG_X4), 16)))
    return 1


oracle = ModuleOracle(module, names, callback)
bb, phl, common, sta, halsta = [0x6000000 + i * 0x10000 for i in range(5)]
for address in (bb, phl, common, sta, halsta):
    oracle.uc.mem_map(address, 0x10000)
source = '''#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define BIT_ULL(n) (1ULL << (n))
static void put_unaligned_le64(u64 v, void *p) { memcpy(p, &v, 8); }
static inline u32 get_unaligned_le32(const void *p) { u32 v; memcpy(&v, p, 4); return v; }
''' + (root / 'artifacts/live-radio-pe-ni/rate.h').read_text() + '''
void native(u8 *p, u8 id, u16 legacy, u16 ht, bool sgi) {
    rtl_ax_ra_ht20(p, id, legacy, ht, sgi);
}
void native_vht(u8 *p, u8 id, u16 legacy, u16 mcs, bool sgi) {
    rtl_ax_ra_vht80(p, id, legacy, mcs, sgi);
}
int native_ppdu(const u8 *p, unsigned bytes, struct rtl_ax_phy_signal *signals) {
    return rtl_ax_ppdu_signal(p, bytes, signals);
}
'''
vectors = 0
with tempfile.TemporaryDirectory(prefix='tyfza1-ra-contract-') as temp:
    c = Path(temp) / 'native.c'
    c.write_text(source)
    library = Path(temp) / 'native.so'
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Werror', str(c), '-o', str(library)], check=True)
    native = ctypes.CDLL(str(library)).native
    native.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_bool]
    for macid in (1, 7, 63, 127):
        for ht in (0x00ff, 0xffff, 0x7f7f, 0x0f03):
            for legacy in (0xfff, 0xff5, 0x011, 0x004):
                for sgi in (False, True):
                    for address in (bb, phl, common, sta, halsta):
                        oracle.uc.mem_write(address, bytes(0x10000))
                    oracle.word(bb, phl)
                    oracle.word(bb + 8, common)
                    oracle.word(bb + (macid + 2) * 8, sta)
                    oracle.uc.mem_write(bb + 3092, struct.pack('<I', 32))
                    oracle.uc.mem_write(phl + 0x58fa, b'\x36')
                    oracle.uc.mem_write(phl + 0x5875, b'\x02')
                    oracle.word(sta + 2688, halsta)
                    oracle.uc.mem_write(sta + 28, bytes([macid]))
                    oracle.uc.mem_write(sta + 2332, b'\x0b')
                    oracle.uc.mem_write(sta + 2656, struct.pack('<I', (2 if ht >> 8 else 1) << 20))
                    oracle.uc.mem_write(sta + 0x9d0, struct.pack('<I', int(sgi) << 28))
                    oracle.uc.mem_write(halsta + 140, b'\x02')
                    oracle.word(halsta + 152, legacy | (ht & 255) << 12 | (ht >> 8) << 24)
                    commands.clear()
                    assert oracle.call('rtw_halbb_raregistered', bb, sta) == 1
                    output = ctypes.create_string_buffer(16)
                    native(output, macid, legacy, ht, sgi)
                    assert len(commands) == 1 and output.raw == commands[0], (macid, ht, legacy, sgi, output.raw.hex(), commands)
                    vectors += 1
    vht = ctypes.CDLL(str(library)).native_vht
    vht.argtypes = native.argtypes
    vht_vectors = 0
    for macid in (1, 7, 63, 127):
        for mcs in (0xfff0, 0xfff5, 0xfffa, 0xfffc, 0xfffd, 0xfffe):
            for sgi in (False, True):
                for address in (bb, phl, common, sta, halsta):
                    oracle.uc.mem_write(address, bytes(0x10000))
                oracle.word(bb, phl)
                oracle.word(bb + 8, common)
                oracle.word(bb + (macid + 2) * 8, sta)
                oracle.uc.mem_write(bb + 3092, struct.pack('<I', 8))
                oracle.uc.mem_write(phl + 0x58fa, b'\x36')
                oracle.uc.mem_write(phl + 0x5875, b'\x02')
                oracle.word(sta + 2688, halsta)
                oracle.uc.mem_write(sta + 28, bytes([macid]))
                oracle.uc.mem_write(sta + 40, struct.pack('<I', 1))
                oracle.uc.mem_write(sta + 44, b'\x02')
                oracle.uc.mem_write(sta + 2332, b'\x14')
                oracle.uc.mem_write(sta + 2656, struct.pack('<I', (1 if mcs >> 2 & 3 == 3 else 2) << 20))
                oracle.uc.mem_write(sta + 0x9d0, struct.pack('<I', int(sgi) << 30))
                oracle.uc.mem_write(halsta + 140, b'\x02')
                mask = 0xff0
                for i in range(2):
                    cap = mcs >> (i * 2) & 3
                    if cap != 3: mask |= ((1 << (8 + cap)) - 1) << (12 + i * 12)
                oracle.word(halsta + 152, mask)
                commands.clear()
                assert oracle.call('rtw_halbb_raregistered', bb, sta) == 1
                output = ctypes.create_string_buffer(16)
                vht(output, macid, 0xff0, mcs, sgi)
                assert len(commands) == 1 and output.raw == commands[0], (macid, mcs, sgi, output.raw.hex(), commands)
                vht_vectors += 1
    ppdu = ctypes.CDLL(str(library)).native_ppdu
    ppdu.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
    ppdu_vectors = 0
    for users in (1, 2, 7, 8):
        for rx_count in (False, True):
            for plcp in (0, 16):
                for rpl in (20, 80, 150, 210):
                    for average, paths in ((73, (72, 77)), (81, (0, 83))):
                        header = bytes([0x8d, 4, 0, average, *paths, 0, 0])
                        phy = header + bytes([1, rpl]) + bytes(22)
                        data = struct.pack('<II', users | int(rx_count) << 29, plcp // 8 << 16)
                        data += b''.join(struct.pack('<I', 3 | (i + 1) << 8) for i in range(users))
                        data += bytes((users & 1) * 4 + (96 if rx_count else 0) + plcp) + phy
                        oracle.uc.mem_write(bb, bytes(0x10000))
                        oracle.uc.mem_write(phl, header)
                        assert oracle.call('halbb_physts_ie_hdr', bb, phl, sta, common) == 1
                        oracle.uc.mem_write(bb + 0x3339, bytes([rpl]))
                        oracle.uc.mem_write(bb + 0x3334, b'\x01')
                        oracle.call('halbb_physts_fd_rpl_2_rssi_cvrt', bb)
                        expected = bytes(oracle.uc.mem_read(bb + 0x3390, 1)) + bytes(oracle.uc.mem_read(bb + 0x3388, 2))
                        out = ctypes.create_string_buffer(32)
                        assert ppdu(data, len(data), out) == users
                        for i in range(users):
                            assert out.raw[i * 4:i * 4 + 4] == bytes([i + 1]) + expected
                        for size in range(len(data) - 8):
                            assert ppdu(data, size, out) < 0
                        ppdu_vectors += 1
report = {'native_command_equals_original_ARM64': True, 'vectors': vectors,
          'VHT80_original_command_vectors': vht_vectors,
          'MAC_PPDU_layout_and_original_RPL_conversion_vectors': ppdu_vectors,
          'chip': '8192XB', 'PHY': 'HT20', 'unknown_RSSI_init_level': 0,
          'original_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'H2C_publication_mocked': True, 'hardware_test': False}
(root / 'logs/radio-ra-native-contract.json').write_text(json.dumps(report, indent=2) + '\n')
print(f'Native firmware RA: {vectors} HT20, {vht_vectors} VHT80, {ppdu_vectors} PPDU/RPL vectors passed')
