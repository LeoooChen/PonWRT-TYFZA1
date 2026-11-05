#!/usr/bin/env python3
"""Replay WP reuse against linked PE code while PCIe retains eight consumed WDs."""
from collections import deque
from pathlib import Path
import json
import struct
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                               UC_MIPS_REG_A3, UC_MIPS_REG_SP, UC_MIPS_REG_RA,
                               UC_MIPS_REG_PC)

directory = Path(sys.argv[1])
before = '--before' in sys.argv
binary = (directory / 'pe-tx.bin').read_bytes()
symbols = {s[2]: int(s[0], 16) for line in subprocess.check_output(
    ['llvm-nm-18', str(directory / 'pe-wd.elf')], text=True).splitlines()
    if len(s := line.split()) == 3}
base, ring, pages, headers, payload, request, result = (
    0x04000000, 0x05000000, 0x05100000, 0x05400000, 0x06000000, 0x04004000, 0x04003000)
mb = base + 0x2000
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
for address, size in ((0x1fc00000, 0x10000), (base, 0x20000), (ring, 0x4000),
                      (pages, 0x32000), (headers, 0xc000), (payload, 0x10000)):
    u.mem_map(address, size)
u.mem_write(0x1fc00000, binary)


def put(address, value): u.mem_write(address, struct.pack('<I', value))
def get(address): return struct.unpack('<I', u.mem_read(address, 4))[0]


for index, value in ((11, ring), (12, pages), (14, headers), (24, 1)):
    put(mb + index * 4, value)
u.mem_write(mb + 64, bytes.fromhex('0200000000010000'))
u.mem_write(payload + 80, bytes.fromhex('0200000000020200000000030800') + bytes(1500))
retained = [deque() for _ in range(6)]
overwrites = []
submissions = 0
for number in range(800):
    for channel in range(6):
        # Reuse one completed WP across all six channels, alternating a large
        # three-address NI aggregate and a short one-address ARM frame.
        ni = number % 2 == 0
        wp = 256 if ni else 0
        count = 3 if ni else 1
        lengths = [1434] * count if ni else [60]
        total = 26 + sum(n + 8 for n in lengths) + 2 * (count - 1) if ni else 60
        r = [0] * 32
        r[0], r[1] = wp, channel
        r[2], r[3] = count << 24 | 26 << 16 | total, 4
        r[4] = (1 << 24 | 1 << 18 | 1 << 21) if ni else 0
        for i, size in enumerate(lengths):
            r[8 + i * 2], r[9 + i * 2] = payload + i * 2048 + 80, size
        u.mem_write(request, struct.pack('<32I', *r))
        slot = get(mb + (32 + channel) * 4)
        for reg, value in ((UC_MIPS_REG_A0, mb | 0xa0000000),
                           (UC_MIPS_REG_A1, base | 0xa0000000),
                           (UC_MIPS_REG_A2, request | 0xa0000000),
                           (UC_MIPS_REG_A3, result | 0xa0000000),
                           (UC_MIPS_REG_SP, (base + 0x1fff0) | 0xa0000000),
                           (UC_MIPS_REG_RA, 0x9fc0fff0)):
            u.reg_write(reg, value)
        u.emu_start(symbols['pe_build_tx'], 0x9fc0fff0, count=10000)
        assert u.reg_read(UC_MIPS_REG_PC) == 0x9fc0fff0
        bd = ring + (channel * 256 + slot) * 8
        used, address = get(bd) & 0xffff, get(bd + 4)
        page = bytes(u.mem_read(address, 128))
        assert struct.unpack_from('<I', page, 56)[0] & 0xfff == wp
        assert used == 64 + count * 6
        assert sum(struct.unpack_from('<H', page, 64 + i * 6)[0] & 2047
                   for i in range(count)) == total
        for queue in retained:
            for old_address, old_page in queue:
                if bytes(u.mem_read(old_address, 128)) != old_page:
                    overwrites.append((number, channel, hex(old_address)))
        if before and overwrites:
            print('REPRODUCED: WP reuse overwrites a retained PCIe WD:', overwrites[0])
            sys.exit(0)
        assert not overwrites, overwrites[:1]
        # At maximum hardware occupancy, retain 255 outstanding + 8 consumed.
        retained[channel].append((address, page))
        if len(retained[channel]) > 263:
            retained[channel].popleft()
        submissions += 1
assert not before, 'old code unexpectedly preserved retained WDs'
print(json.dumps({'linked_PE_submissions': submissions, 'channels': 6,
                  'retained_per_channel': 263, 'WP_reuse_across_channels': True,
                  'TXBD_and_WD_pool_wraps': True, 'descriptor_overwrites': 0,
                  'scope': 'Real linked builder; retained-memory lifetime model, '
                           'not proof of the physical fault trigger.'}, indent=2))
