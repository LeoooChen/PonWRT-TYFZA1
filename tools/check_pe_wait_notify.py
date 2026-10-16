#!/usr/bin/env python3
"""Execute production PE notifications while another peer still owns a WP."""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
parser.add_argument('--before', action='store_true')
args = parser.parse_args()
binary = (args.directory / 'pe-tx.bin').read_bytes()
B, MB, BAR, NI, RPQ, FORWARD, PAYLOAD = 0x4000000, 0x4002000, 0x12000000, 0x10000000, 0x6000000, 0x6100000, 0x6200000
rows = []
for kind in ('RPQ-with-other-peer', 'retired-queue-with-other-peer', 'last-WP', 'RPQ-no-waiter'):
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, size in ((0x1fc00000, 0x4000), (B, 0x20000), (BAR, 0x10000), (NI, 0x10000), (RPQ, 0x40000), (FORWARD, 0x40000), (PAYLOAD, 0x1000), (0x10020000, 0x1000)):
        u.mem_map(at, size)
    u.mem_write(0x1fc00000, binary)
    def put(at, value): u.mem_write(at, struct.pack('<I', value))
    def get(at): return struct.unpack('<I', u.mem_read(at, 4))[0]
    for index, value in ((0, 0x50455744), (2, BAR | 0xa0000000), (3, 1), (30, 1),
                         (68, RPQ), (72, FORWARD), (78, 1), (116, 0x6380), (117, 6), (140, MB | 0xa0000000)):
        put(MB + index * 4, value)
    if kind == 'retired-queue-with-other-peer':
        # Peer8 owns an unrelated live WP. Peer7 has one queued TID3 BID.
        put(MB + 24 * 4, 1)
        put(B + 0xc400, PAYLOAD + 128)
        put(B + 0xc404, 8 << 8 | 1 << 15)
        put(MB + 38 * 4, 1)
        put(MB + 170 * 4, 8)
        put(B + 0x19000 + 7 * 4, 8)
        put(B + 0x19200 + 7 * 4, 8)
        put(B + 0x11800 + 7 * 4, 1)
        for base in (0x13000, 0x18000): put(B + base + (7 * 8 + 3) * 4, 1)
        for base in (0x15000, 0x16000): put(B + base + (7 * 8 + 3) * 4, PAYLOAD)
        u.mem_write(PAYLOAD, struct.pack('<4I', PAYLOAD + 48, 1514, 7, 3))
    else:
        put(B + 0xc400, PAYLOAD)
        put(B + 0xc404, 7 << 8 | 1 << 15)
        if kind in ('RPQ-with-other-peer', 'RPQ-no-waiter'):
            put(MB + 30 * 4, 2)
            put(B + 0xc408, PAYLOAD + 128)
            put(B + 0xc40c, 8 << 8 | 1 << 15)
            if kind == 'RPQ-no-waiter': put(MB + 78 * 4, 0)
        else:
            put(MB + 78 * 4, 2)  # Two host waiters: PE must leave their count alone.
            put(NI + 0x6340, 1)
            put(NI + 0x6380, 1 << 31)
            put(B + 0x18000 + 7 * 32, 1)
        raw = struct.pack('<II', 24 | 0xc000 | 1 << 16, 7 << 24 | 4) + bytes(12) + struct.pack('<I', 256 << 16 | 7)
        u.mem_write(RPQ, raw)
        put(BAR + 0x121c, 1 << 16)
    u.reg_write(UC_MIPS_REG_A0, MB | 0xa0000000)
    u.reg_write(UC_MIPS_REG_SP, (B + 0x1fff0) | 0xa0000000)
    u.emu_start(0xbfc00400, 0xbfc03ffc, count=25000)
    requests = get(MB + 67 * 4)
    other_active = bool(get(B + 0xc400 + (0 if kind.startswith('retired') else 8))) if kind != 'last-WP' else False
    if kind == 'last-WP':
        assert get(MB + 30 * 4) == 0
        assert get(MB + 78 * 4) == (0 if args.before else 2)
    else:
        assert other_active and get(MB + 30 * 4)
        assert bool(requests) == (not args.before and kind != 'RPQ-no-waiter')
    rows.append(dict(case=kind, event_requests=requests, unrelated_peer_still_owns_WP=other_active,
                     host_waiter_count_after=get(MB + 78 * 4)))
print(json.dumps(dict(PE_sha256=hashlib.sha256(binary).hexdigest(), cases=rows,
                     scope='Actual linked pe_main, RPQ and NI selection/recycling. EQ blocked for partial-completion cases; PCIe/NI registers are modeled. Checks event requests, not interrupt delivery or elapsed time.'), indent=2))
