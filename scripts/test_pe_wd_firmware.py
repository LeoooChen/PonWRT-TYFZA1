#!/usr/bin/env python3
"""Execute the linked PE C firmware and compare it with native WD/WP encoders."""
import ctypes
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_PC


def function(name):
    text = (driver / 'tx.c').read_text()
    start = text.rfind('\n', 0, text.index(name + '(')) + 1
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


common = '''#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define RTL_AX_BIT(n) BIT(n)
#define RTL_AX_AP_TRANSPORT_H
#define min(a,b) ((a)<(b)?(a):(b))
static void rtl_ax_store32(u8 *p,u32 v){memcpy(p,&v,4);}
static u32 rtl_ax_ap_read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
'''
for name in ('tx-format.h', 'security.h'):
    common += '\n'.join(line for line in (driver / name).read_text().splitlines()
                        if not line.startswith('#include')) + '\n'
common += function('rtl_ax_tx_wd') + function('rtl_ax_tx_checksum')
common += '''
void expected(u8 *page,const u32 *r){
 unsigned ring=r[1]&7,flags=r[4],ag=r[5]&255,count=0;
 static const u8 channels[]={0,1,2,3,8,9};memset(page,0,128);
 for(unsigned s=0;s<(r[2]>>24);s++)count+=rtl_ax_tx_wp_addresses(page+64+count*6,r[8+s*2],r[9+s*2],s+1==(r[2]>>24));
 rtl_ax_tx_wd(page,r[2]&65535,count,r[3]&511,(r[3]>>12)&7,(r[3]>>16)&3,flags&4095,
 (r[2]>>16)&63,!!(flags&BIT(13)),!!(flags&BIT(14)),channels[ring],ring<4?ring:ring==4?0x12:0x11,
 !!(flags&BIT(12)),flags>>24,ag,(r[5]>>8)&7);
 if(flags&BIT(15))rtl_ax_security_tx(page,(r[5]>>16)&127,(r[5]>>24)&3,(u64)r[7]<<32|r[6]);
 if(flags&BIT(16)){rtl_ax_store32(page,rtl_ax_ap_read32(page)|BIT(6));rtl_ax_store32(page+4,rtl_ax_ap_read32(page+4)|6|((r[5]>>24)&3)<<4);rtl_ax_store32(page+40,rtl_ax_ap_read32(page+40)|BIT(8)|((r[5]>>16)&127));}
 if(flags&BIT(17))rtl_ax_tx_checksum(page);
 rtl_ax_tx_wp_sequence(page+56, r[0]|(flags&BIT(12)?BIT(14):0));
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='pe-wd-reference-') as directory:
    work = Path(directory)
    (work / 'native.c').write_text(common)
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', str(work / 'native.c'), '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    output = ctypes.create_string_buffer(128)
    code = (Path(sys.argv[1]) if len(sys.argv)>1 else root / 'artifacts/live-radio-pe-wd/pe-tx.bin').read_bytes()
    words = list(struct.unpack('<%dI' % (len(code) // 4), code))
    # Realtek CP3 configures instruction RAM; Unicorn has no such controller.
    for i, word in enumerate(words):
        if word in (0x4c880000, 0x4c880800, 0x4cc80000, 0x4cc81000): words[i] = 0
        if word == 0x4c084000: words[i] = 0x24080000
    base, mailbox, ring_base, pages, bar = 0x04000000, 0x04002000, 0x05000000, 0x05100000, 0x12000000
    words[0] |= (mailbox | 0xa0000000) >> 16
    words[1] |= mailbox & 65535
    words[2] |= (base | 0xa0000000) >> 16
    words[0x380 // 4] |= (mailbox | 0xa0000000) >> 16
    words[0x384 // 4] |= mailbox & 65535
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, length in ((0x1fc00000, (len(code) + 4095) & ~4095), (base, 65536), (ring_base, 16384), (pages, 32768), (bar, 65536)):
        u.mem_map(at, length)
    u.mem_write(0x1fc00000, struct.pack('<%dI' % len(words), *words))
    u.mem_map(0x10020000, 4096)

    def put(at, value): u.mem_write(at, struct.pack('<I', value))
    def get(at): return struct.unpack('<I', u.mem_read(at, 4))[0]

    put(mailbox + 8, 0xb2000000)
    put(mailbox + 44, ring_base)
    put(mailbox + 48, pages)
    doorbells = []
    u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
               doorbells.append((at, size, value)) if bar <= at < bar + 65536 else None,
               begin=bar, end=bar + 65535)
    pc = 0xbfc00000

    def run():
        global pc
        u.emu_start(pc, 0xbfc03000, count=4000)
        pc = u.reg_read(UC_MIPS_REG_PC)

    run()
    assert get(mailbox) == 0x50455744 and not doorbells
    vectors = 0
    for sequence in range(1024):
        ring = sequence % 6
        segments = (1500,) if sequence % 3 == 0 else (30, 1500, 2048, 500) if sequence % 3 == 1 else (11454,)
        flags = sequence & 4095 | (sequence & 1) << 12 | ((sequence >> 1) & 1) << 13 | ((sequence >> 2) & 1) << 14
        crypto = sequence % 3
        flags |= (1 << (14 + crypto)) if crypto else 0
        flags |= ((sequence >> 3) & 1) << 17 | (1 + sequence % 126) << 24
        r = [0] * 32
        r[0] = sequence & 255
        r[1] = ring | (sequence & 255) << 8 | ((sequence + 1) & 255) << 16
        r[2] = sum(segments) | (24 + (sequence % 2) * 2) << 16 | len(segments) << 24
        r[3] = (0x100 + sequence % 10) | (sequence % 2) << 12 | (sequence % 3) << 16
        r[4] = flags
        r[5] = (64 if sequence & 1 else 0) | (sequence % 8) << 8 | (sequence % 128) << 16 | (sequence % 4) << 24
        r[6], r[7] = 0x03020100 + sequence, 0x0504
        for s, length in enumerate(segments): r[8 + s * 2], r[9 + s * 2] = 0x01000000 + s * 0x10000, length
        request = (ctypes.c_uint32 * 32)(*r)
        native.expected(output, request)
        u.mem_write(base + 0x4000 + (sequence & 255) * 128, struct.pack('<32I', *r))
        put(mailbox + 32, (sequence + 1) & 255)
        put(mailbox + 12, sequence * 4 + 1)
        run()
        assert get(mailbox + 36) == (sequence + 1) & 255
        actual = bytes(u.mem_read(pages + r[0] * 128, 128))
        assert actual == output.raw, (sequence, actual.hex(), output.raw.hex())
        count = sum((n + 2046) // 2047 for n in segments)
        txbd = struct.unpack('<II', u.mem_read(ring_base + (ring * 256 + (sequence & 255)) * 8, 8))
        assert txbd == (0x40000000 | (64 + count * 6), pages + r[0] * 128)
        assert doorbells[-1] == (bar + [0x1058, 0x105c, 0x1060, 0x1064, 0x1078, 0x107c][ring], 2, (sequence + 1) & 255)
        put(mailbox + 12, sequence * 4 + 2)
        before = len(doorbells)
        run()
        assert get(mailbox + 16) == sequence * 4 + 2 and len(doorbells) == before
        vectors += 1
    report = {'linked_MIPS_C_firmware_vectors': vectors, 'native_WD_WP_bytes_exact': True,
              'six_TXBD_rings': True, 'scatter_single_multi_2047_boundary': True,
              'SW_PN_and_HW_PN_checksum': True, 'paused_requests_not_submitted': True,
              'hardware_test': False}
    (root / 'logs/pe-wd-firmware-test.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Linked PE WD C firmware: 1024 native WD/WP/TXBD byte matches, scatter, crypto, checksum, producer wraps and pause passed')
