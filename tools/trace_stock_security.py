#!/usr/bin/env python3
"""Execute the original C/XB Security CAM, DCTL and encrypted TX encoders."""
from pathlib import Path
import struct
import ctypes
import subprocess
import tempfile
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko'
names = [base + '_' + chip for chip in ('8852c', '8192xb')
         for base in ('fill_sec_cam_info', 'upt_dctl_secinfo_entry',
                      'mac_upd_dctl_info', 'txdes_proc_data', 'mac_txdesc_len')]
names.append('h2c_pkt_drop')
adapter, ops, hwinfo, table, entry, info, output, packet, payload = [
    0x6000000 + i * 0x10000 for i in range(9)]
size = 0

def callback(name, args):
    global size
    if name in ('printk', 'h2cb_free', 'h2c_pkt_build_txd', 'platform_TX', 'h2c_pkt_set_hdr', 'h2c_end_flow'):
        return 0
    if name == 'h2cb_alloc':
        return packet
    if name == 'h2cb_put':
        size = args[1]
        oracle.uc.mem_write(payload, bytes(512))
        return payload
    if name == 'h2c_agg_enqueue':
        return 1
    if name == 'platform_memcpy':
        oracle.uc.mem_write(args[1], bytes(oracle.uc.mem_read(args[2], args[3])))
        return 0
    if name == 'platform_memset':
        oracle.uc.mem_write(args[1], bytes([args[2] & 255]) * args[3])
        return 0
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, names, callback)
for address in (adapter, ops, hwinfo, table, entry, info, output, packet, payload):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(adapter + 24, ops)
oracle.word(adapter + 88, hwinfo)
oracle.uc.mem_write(adapter + 33, b'\x04')
oracle.uc.mem_write(hwinfo + 8, struct.pack('<I', 2))
oracle.word(hwinfo + 112, table)
oracle.word(table + 7 * 8, entry)
oracle.word(ops + 48, oracle.callback('platform_TX'))
oracle.word(ops + 88, oracle.callback('platform_memcpy'))
oracle.word(ops + 96, oracle.callback('platform_memset'))
dbcc, wmm = 0x6090000, 0x60a0000
oracle.uc.mem_map(dbcc, 0x10000)
oracle.uc.mem_map(wmm, 0x10000)
oracle.word(adapter + 1464, dbcc)
oracle.word(dbcc + 64, wmm)
oracle.uc.mem_write(wmm, bytes([4]) * 32)
key = bytes(range(16))
native_dir = tempfile.TemporaryDirectory(prefix='radio-security-', dir=root / 'artifacts')
native_path = Path(native_dir.name)
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/security.h'
(native_path / 'native.c').write_text('''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#define RTL_AX_AP_TRANSPORT_H
#define RTL_AX_BIT(n) (1U << (n))
#define BIT(n) RTL_AX_BIT(n)
#define IEEE80211_NUM_TIDS 16
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
static unsigned int rtl_ax_ap_read32(const u8 *p) { unsigned int x; memcpy(&x,p,4); return x; }
static void rtl_ax_store32(u8 *p, unsigned int x) { memcpy(p,&x,4); }
''' + '#include "' + str(header) + '"\n' + '#include "' + str(header.with_name('role.h')) + '"\n' + '''
void native_cam(u8 *out, unsigned int index, const u8 *key) { rtl_ax_security_cam(out,index,key); }
void native_tx(u8 *out, unsigned int index, unsigned int keyid, u64 pn) { rtl_ax_security_tx(out,index,keyid,pn); }
void native_dctl(u8 *out, unsigned int slot) {
 struct rtl_ax_security_role role = {.valid=1U << slot,.tx_keyid=1};
 role.keyid[slot]=1;role.cam[slot]=63;rtl_ax_security_dctl(out,7,&role);
}
void native_drop(u8 *out, unsigned int macid, unsigned int ac) { rtl_ax_station_drop(out,macid,ac); }
''')
subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                str(native_path / 'native.c'), '-o', str(native_path / 'native.so')], check=True)
native = ctypes.CDLL(str(native_path / 'native.so'))
native.native_tx.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint64]
native_output = ctypes.create_string_buffer(68)
checks = 0
for chip in ('8852c', '8192xb'):
    for index in (0, 1, 63, 127):
        oracle.uc.mem_write(info, bytes([index, 0, 20, 6]) + key)
        assert oracle.call('fill_sec_cam_info_' + chip, adapter, info, output, 0) == 0
        assert bytes(oracle.uc.mem_read(output, 24)) == struct.pack('<II', index | 20 << 16, 6) + key
        native.native_cam(native_output, index, key)
        assert native_output.raw[:24] == bytes(oracle.uc.mem_read(output, 24))
        for keyid in range(4):
            data = bytearray(96)
            struct.pack_into('<H', data, 8, 1500)
            struct.pack_into('<H', data, 14, 7)
            data[20], data[21], data[24] = 13, 56, 6 << 4 | keyid << 1 | 9
            data[32], data[33], data[34] = index, 0x30, 1
            data[26:32] = bytes.fromhex('010203040506')
            oracle.uc.mem_write(info, bytes(data))
            length = oracle.call('mac_txdesc_len_' + chip, adapter, info)
            assert oracle.call('txdes_proc_data_' + chip, adapter, info, output, length) == 0
            words = oracle.unpack(output, '14I')
            assert words[1] & 0x7f == 64 | 6 | keyid << 4, (chip, words)
            assert words[10] & 0x1ff == 256 | index, (chip, words)
            assert words[4] >> 16 == 0x0201 and words[5] == 0x06050403
            ctypes.memset(native_output, 0, 68)
            native.native_tx(native_output, index, keyid, 0x060504030201)
            built = struct.unpack('<17I', native_output.raw)
            assert built[1] == words[1] & 0x7f
            assert all(built[w] == words[w] for w in (4, 5, 10)), (chip, built, words)
            checks += 1
    for slot in range(7):
        data = bytearray(32)
        data[8], data[10], data[11 + slot], data[18 + slot] = 1, 1 << slot, 1, 63
        oracle.uc.mem_write(entry, bytes(data))
        assert oracle.call('upt_dctl_secinfo_entry_' + chip, adapter, 7, slot, 0) == 0
        words = oracle.unpack(payload, '17I')
        assert size == 68 and words[0] == 0x87
        assert words[5] == (1 << 9) | (1 << (18 + slot * 2)), (chip, slot, words)
        assert words[6] & 255 == 1 << slot
        assert bytes(oracle.uc.mem_read(payload + 25 + slot, 1)) == b'\x3f'
        native.native_dctl(native_output, slot)
        built = struct.unpack('<17I', native_output.raw)
        assert built[0] == words[0] and built[5] == words[5] | 2 << 16
        assert built[6:8] == words[6:8]
        checks += 1
for macid in range(128):
    for ac in range(4):
        metadata = bytearray(8)
        metadata[0], metadata[4] = ac, macid
        oracle.uc.mem_write(info, bytes(metadata))
        assert oracle.call('h2c_pkt_drop', adapter, info, 0, 0) == 0
        native.native_drop(native_output, macid, ac)
        assert native_output.raw[:24] == bytes(oracle.uc.mem_read(payload, 24))
        checks += 1
native_dir.cleanup()
print(f'Native Security CAM, DCTL, TX and SCC packet drop match original C/XB: {checks} checks passed')
