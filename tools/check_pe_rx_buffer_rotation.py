#!/usr/bin/env python3
"""Execute RXBD replacement while NI retains its final two payload references."""
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC


def instance(directory):
    directory = Path(directory)
    symbols = subprocess.check_output(['llvm-nm-18', str(directory / 'pe-wd.elf')], text=True)
    entry = next(int(s.split()[0], 16) for s in symbols.splitlines() if s.endswith(' pe_rxq'))
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    base, mb, bar, vp, buffers, rxbd = 0x04000000, 0x04002000, 0x11000000, 0x130016a0, 0x06000000, 0x07000000
    for address, size in [(0x1fc00000, 0x10000), (base, 0x20000), (bar, 0x10000),
                          (0x13000000, 0x2000), (buffers, 258 * 12288), (rxbd, 0x1000),
                          (0x05000000, 0x20000), (0x07100000, 0x1000)]:
        u.mem_map(address, size)
    u.mem_write(0x1fc00000, (directory / 'pe-tx.bin').read_bytes())
    put = lambda at, value: u.mem_write(at, struct.pack('<I', value & 0xffffffff))
    get = lambda at: struct.unpack('<I', u.mem_read(at, 4))[0]
    for index, value in [(2, bar | 0xa0000000), (80, buffers), (88, vp | 0xa0000000),
                          (134, rxbd)]:
        put(mb + index * 4, value)
    for slot in range(256):
        put(base + 0x17400 + slot * 4, slot)
        put(rxbd + slot * 8, 12288)
        put(rxbd + slot * 8 + 4, buffers + slot * 12288)
    put(base + 0x17800, 257)
    put(base + 0x17804, 258)

    def call():
        for reg, value in [(UC_MIPS_REG_A0, mb | 0xa0000000),
                           (UC_MIPS_REG_SP, (base + 0xfff0) | 0xa0000000),
                           (UC_MIPS_REG_RA, 0x9fc0fff0)]:
            u.reg_write(reg, value)
        u.emu_start(entry, 0x9fc0fff0, count=100000)
        assert u.reg_read(UC_MIPS_REG_PC) == 0x9fc0fff0

    return u, base, mb, bar, vp, buffers, rxbd, put, get, call


def completed_pair(directory, end):
    u, base, mb, bar, vp, buffers, rxbd, put, get, call = instance(directory)
    start = (end - 2) & 0x7fffffff
    put(mb + 81 * 4, 2)
    put(mb + 91 * 4, end)
    put(vp + 0x1c, end & 63)
    put(bar + 0x1218, 2 << 16)
    original = []
    for slot in range(2):
        raw = struct.pack('<I', 0x1234c064) + bytes([slot + 1]) * 128
        u.mem_write(buffers + slot * 12288, raw)
        original.append(raw)
        put(base + 0xf000 + slot * 4, 0x80000000 | ((start + slot + 1) & 0x7fffffff))
    call()
    addresses = [get(rxbd + slot * 8 + 4) for slot in range(2)]
    return u, base, mb, bar, vp, buffers, rxbd, put, get, call, original, addresses


old = completed_pair(sys.argv[1], 2)
assert old[8](old[2] + 83 * 4) == 2
assert old[-1] == [old[5], old[5] + 12288]
assert old[8](old[5]) == 0  # Original exposes still-retained NI payloads to PCIe.

records = []
for end in (2, 64, 1 << 30, 0, 1):
    u, base, mb, bar, vp, buffers, rxbd, put, get, call, original, addresses = completed_pair(sys.argv[2], end)
    assert get(mb + 83 * 4) == 2 and get(bar + 0x1218) & 255 == 2
    assert addresses == [buffers + 256 * 12288, buffers + 257 * 12288]
    for slot, raw in enumerate(original):
        assert bytes(u.mem_read(buffers + slot * 12288, len(raw))) == raw
    # Exercise a complete RXBD lap of ARM-owned management frames with no NI progress.
    for n in range(256):
        slot = (n + 2) & 255
        address = get(rxbd + slot * 8 + 4)
        u.mem_write(address, struct.pack('<I', 0x4567c080) + bytes([n & 255]) * 128)
        put(base + 0xf000 + slot * 4, 1 << 30)
        put(mb + 81 * 4, (slot + 1) & 255)
        put(bar + 0x1218, ((slot + 1) & 255) << 16 | slot)
        call()
        assert get(mb + 83 * 4) == (slot + 1) & 255
    for slot, raw in enumerate(original):
        assert bytes(u.mem_read(buffers + slot * 12288, len(raw))) == raw
    # Advancing one descriptor releases only the older payload; two release both.
    for advance in (1, 2):
        put(vp + 0x1c, (end + advance) & 63)
        call()
        assert get(buffers) == 0
        assert (get(buffers + 12288) == 0) == (advance == 2)
    assert sorted([get(base + 0x17800), get(base + 0x17804)]) == [1, 2]
    assert get(base + 0x17810) == get(base + 0x17818) == 0
    ids = [(get(rxbd + slot * 8 + 4) - buffers) // 12288 for slot in range(256)]
    assert len(set(ids)) == 256 and set(ids).isdisjoint({0, 1})
    # A new encrypted frame in the replacement buffer must become an NI
    # descriptor for that address, rather than the old fixed slot address.
    peer, ap, dst = bytes.fromhex('067afb78b287'), bytes.fromhex('02e04c076805'), bytes.fromhex('0050c2010200')
    u.mem_write(mb + 64, ap)
    u.mem_write(base + 0xe410, peer)
    put(base + 0x11a08, 1)
    put(0x05010008, 0x80000100)
    put(0x05000400, 0x80000123)
    for index, value in [(89, 0x07100000), (93, 1), (106, 0x05000000),
                          (90, end + 2), (81, 0), (83, 0), (82, 8191)]:
        put(mb + index * 4, value)
    header = struct.pack('<HH', 0x4188, 0) + ap + peer + dst + struct.pack('<H', 0x123 << 4) + bytes(2)
    network = bytearray(46)
    network[0] = 0x45
    network[9] = 17
    frame = header + bytes.fromhex('0200002000000000aaaa030000000800') + network + bytes(12)
    raw = bytearray(36) + frame
    struct.pack_into('<II', raw, 0, len(raw) | 0x1c000, len(frame) | 1 << 31)
    struct.pack_into('<I', raw, 16, 4)
    struct.pack_into('<I', raw, 24, 1 << 28 | 2 << 16)
    u.mem_write(addresses[0], bytes(raw))
    put(bar + 0x1218, 1 << 16)
    call()
    desc = 0x07100000 + ((end + 2) & 63) * 8
    assert get(desc) == addresses[0] + 36 + 26 + 8 - 6
    assert bytes(u.mem_read(get(desc), 60)) == dst + peer + bytes.fromhex('0800') + bytes(network)
    assert get(mb + 81 * 4) == 1 and get(mb + 94 * 4) == 0
    records.append(dict(end_sequence=end, RXBD_returns_with_replacements=True,
                        original_payloads_held_until_two_more_descriptors=True,
                        idle_management_RXBD_lap=256, replacement_addresses_unique=True,
                        next_frame_offloads_from_replacement_without_ARM=True))

print(json.dumps(dict(scope='Actual linked pe_rxq and buffer-ownership helpers. Models delayed NI payload references and PCIe buffer overwrite, not bus timings.',
                      baseline_returns_NI_retained_payloads_to_PCIe=True, cases=records), indent=2))
