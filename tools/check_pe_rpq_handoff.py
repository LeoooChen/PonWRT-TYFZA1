#!/usr/bin/env python3
"""Execute linked RPQ consumption with a full ARM handoff ring."""
from pathlib import Path
import hashlib
import json
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC

directory = Path(sys.argv[1])
old = len(sys.argv) > 2 and sys.argv[2] == '--before'
binary = (directory / 'pe-tx.bin').read_bytes()
BASE, MB, BAR, NI, RPQ, FORWARD, PAYLOAD = (
    0x4000000, 0x4002000, 0x12000000, 0x10000000, 0x6000000, 0x6100000, 0x6200000)


def case(kind):
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for a, n in ((0x1fc00000, 0x10000), (BASE, 0x20000), (BAR, 0x10000),
                 (NI, 0x10000), (RPQ, 0x40000), (FORWARD, 0x40000), (PAYLOAD, 0x1000),
                 (0x10020000, 0x1000)):
        u.mem_map(a, n)
    u.mem_write(0x1fc00000, binary)
    def put(a, v): u.mem_write(a, struct.pack('<I', v))
    def get(a): return struct.unpack('<I', u.mem_read(a, 4))[0]
    u.reg_write(UC_MIPS_REG_A0, MB | 0xa0000000)
    u.reg_write(UC_MIPS_REG_SP, (BASE + 0x1fff0) | 0xa0000000)
    pc = 0xbfc00400
    def run():
        nonlocal pc
        u.emu_start(pc, 0x9fc0fff0, count=10000)
        pc = u.reg_read(UC_MIPS_REG_PC)
    # Full handoff: producer 255, consumer 0. Protect its entire contents.
    sentinel = bytes([0xa5]) * 0x40000
    u.mem_write(FORWARD, sentinel)
    for i, v in ((0, 0x50455744), (2, BAR | 0xa0000000), (3, 1), (30, 1),
                 (68, RPQ), (70, 255), (71, 8191), (72, FORWARD), (73, 255),
                 (74, 0), (116, 0x6380), (117, 6), (140, MB | 0xa0000000)):
        put(MB + i * 4, v)
    put(BASE + 0xc400, PAYLOAD)
    put(BASE + 0xc404, 1 << 8 | 1 << 15 | (1 << 16 if kind == 'EOSP' else 0))
    put(BASE + 0x18020, 2)
    put(PAYLOAD, PAYLOAD + 128)
    put(PAYLOAD + 128, 0)
    report = 256 << 16 | 1
    data = struct.pack('<I', report)
    if kind == 'mixed': data += struct.pack('<I', 9 << 16 | 1)
    type = 7
    if kind == 'C2H': type, data = 10, b'command-ACK'
    if kind == 'old-DFS':
        type, data = 8, bytes(16)
        put(MB + 173 * 4, 256)
    raw = struct.pack('<II', (20 + len(data)) | 0xc000 | 1 << 16, type << 24 | len(data)) + bytes(12) + data
    u.mem_write(RPQ + 255 * 1024, raw)
    put(BAR + 0x121c, 255)
    returns = []
    u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, v, ctx: returns.append(v),
               begin=NI + 0x6384, end=NI + 0x6387)
    run()
    consumed = get(MB + 70 * 4) == 0
    assert consumed == (not old and kind in ('PE-only', 'old-DFS')), kind
    assert bytes(u.mem_read(FORWARD, len(sentinel))) == sentinel
    if not consumed:
        assert get(MB + 75 * 4) == 0 and get(BASE + 0xc404) & (1 << 31) == 0
        assert bytes(u.mem_read(RPQ + 255 * 1024, len(raw))) == raw
        put(MB + 74 * 4, 1)
        run()
        assert get(MB + 70 * 4) == 0
    if type == 7:
        assert get(MB + 75 * 4) == 1
        run()
        assert get(MB + 75 * 4) == 1
        put(NI + 0x6340, 1)
        put(NI + 0x6380, 1 << 31)
        run()
        assert returns == [PAYLOAD | 6, (PAYLOAD + 128) | 6]
        assert get(MB + 30 * 4) == 0 and get(BASE + 0x18020) == 0
    if kind in ('mixed', 'EOSP', 'C2H'):
        expected = data[4:] if kind == 'mixed' else data
        out = bytes(u.mem_read(FORWARD + 255 * 1024, 20 + len(expected)))
        assert out[20:] == expected and get(FORWARD + 255 * 1024 + 4) & 0x3fff == len(expected)
        assert get(MB + 73 * 4) == 0
    else:
        assert get(MB + 73 * 4) == 255
    return {'report': kind, 'consumed_while_ARM_full': consumed, 'completion_processed_once': type == 7}


print(json.dumps({'PE_sha256': hashlib.sha256(binary).hexdigest(),
                  'cases': [case(n) for n in ('PE-only', 'mixed', 'EOSP', 'C2H', 'old-DFS')],
                  'scope': 'Actual linked pe_rpq/ni_recycle; synthetic full ARM ring and RPQ/EQ inputs. '
                           'No timing or hardware throughput model.'}, indent=2))
