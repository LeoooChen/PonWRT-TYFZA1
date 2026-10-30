#!/usr/bin/env python3
"""Execute RX submission against the stock NI descriptor ownership boundary."""
from pathlib import Path
import json
import struct
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_RA

directory = Path(sys.argv[1])
symbols = {p[2]: int(p[0], 16) for line in subprocess.check_output(
    ['llvm-nm-18', str(directory / 'pe-wd.elf')], text=True).splitlines()
    if len(p := line.split()) == 3}
binary = (directory / 'pe-tx.bin').read_bytes()
before = '--before' in sys.argv
base, mailbox, states, payload, descriptors, vp = (
    0x04000000, 0x04002000, 0x08000000, 0x06000000, 0x07000000, 0x13001000)
ap, peer, destination = (bytes.fromhex(s) for s in
    ('020000000042', '02000000004e', '020000000065'))


def run(start, initial, outstanding, frames):
    cpu = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for address, size in ((0x1fc00000, 0x10000), (base, 0x20000),
                          (states, 0x20000), (payload, 0x4000),
                          (descriptors, 0x3000), (vp, 0x1000), (0x10000000, 0x10000)):
        cpu.mem_map(address, size)
    put = lambda address, value: cpu.mem_write(address, struct.pack('<I', value & 0xffffffff))
    get = lambda address: struct.unpack('<I', cpu.mem_read(address, 4))[0]
    cpu.mem_write(0x1fc00000, binary)
    cpu.mem_write(descriptors, bytes([0xa5]) * 8192)
    cpu.mem_write(mailbox + 64, ap)
    cpu.mem_write(base + 0xe410, peer)
    put(base + 0x11a08, 1)
    put(states + 0x10008, 1 << 31 | 1 << 8)
    put(states + 1024, 1 << 31 | 0x123)
    put(states + 1028, 1)
    put(0x10002300, 1 << 31 | 1 << 13)
    put(0x100038c4, 1 << 31)
    for index, value in ((88, vp | 0xa0000000), (89, descriptors),
                         (90, start + outstanding), (91, start), (93, 1),
                         (106, states), (118, initial), (174, 1), (187, base)):
        put(mailbox + index * 4, value)
    put(mailbox + 80 * 4, descriptors + 8192)
    put(descriptors + 8192, payload)
    header = struct.pack('<HH', 0x4188, 0) + ap + peer + destination
    header += struct.pack('<H', 0x123 << 4) + bytes([0x80, 0])
    iv = bytes([2, 0, 0, 0x20, 0, 0, 0, 0])
    ip = bytearray(20)
    ip[0], ip[9] = 0x45, 17
    llc = bytes.fromhex('aaaa030000000800') + ip + bytes(8)
    sub = destination + peer + struct.pack('!H', len(llc)) + llc
    frame = header + iv + (sub + bytes((-len(sub)) & 3)) * (frames - 1) + sub + bytes(12)
    raw = bytearray(36) + frame
    struct.pack_into('<II', raw, 0, len(raw) | 0xc000 | 1 << 16, len(frame) | 1 << 31)
    struct.pack_into('<I', raw, 16, 4)
    struct.pack_into('<I', raw, 24, 1 << 28 | 2 << 16)
    cpu.mem_write(payload, bytes(raw))
    def process():
        for reg, value in ((UC_MIPS_REG_A0, mailbox | 0xa0000000),
                           (UC_MIPS_REG_SP, (base + 0x1fff0) | 0xa0000000),
                           (UC_MIPS_REG_RA, 0x9fc0fff0)):
            cpu.reg_write(reg, value)
        cpu.emu_start(symbols['pe_rx_work'], 0x9fc0fff0, count=100000)
    process()
    sent = (get(mailbox + 90 * 4) - (start + outstanding)) & 0xffffffff
    if not sent:
        assert bytes(cpu.mem_read(payload, len(raw))) == raw
        assert get(states + 1024) == 1 << 31 | 0x123
        assert bytes(cpu.mem_read(descriptors, 8192)) == bytes([0xa5]) * 8192
        if frames <= 63:
            assert get(mailbox + 94 * 4) == 0, 'NI backpressure became ARM fallback'
            assert get(mailbox + 175 * 4) == 0
            # Polling while still full neither submits nor changes ownership.
            process()
            assert get(mailbox + 94 * 4) == 0 and get(mailbox + 175 * 4) == 0
            put(mailbox + 91 * 4, start + outstanding)
            process()
            assert (get(mailbox + 90 * 4) - (start + outstanding)) & 0xffffffff == frames
            assert get(mailbox + 94 * 4) == 0 and get(mailbox + 175 * 4) == 1
    return sent


# Fresh queue: two free slots plus the empty-ring sentinel. After consumption,
# the last two descriptors remain prefetched, just as in ca-ne.ko completion.
cases = [(0, 0, 1014, 7, 7), (0, 0, 1015, 7, 0),
         (1024, 0, 1012, 7, 7), (1024, 0, 1013, 7, 0),
         (1024, 0, 1020, 3, 0), (0xfffffffe, 0xfffffffc, 1013, 7, 0),
         (0, 0, 0, 63, 63), (0, 0, 0, 64, 0)]
results = [run(*row[:4]) for row in cases]
if before:
    assert results[4] == 3
    print('Original PE overwrites retained descriptor1022 when read=1024, write=2044 and three frames arrive.')
else:
    assert results == [row[4] for row in cases], results
    print(json.dumps({'actual_linked_PE': True, 'cases': len(cases),
                      'retained_prefetch_descriptors_unchanged': True,
                      'insufficient_credit_preserves_entire_MPDU': True,
                      'counter_wrap_checked': True, 'hardware_test': False}))
