#!/usr/bin/env python3
"""Execute the assembled PE consumer across EPP wraps and delayed EQ credits."""
from pathlib import Path
import json
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_PC

code = bytearray((root / 'artifacts/live-pe-packet/pe-rx.bin').read_bytes())
base, ni = 0x04000000, 0x10000000
mailbox = base + 0x800
words = list(struct.unpack('<%dI' % (len(code) // 4), code))
for offset in (0, 0x380 // 4):
    words[offset] |= (mailbox | 0xa0000000) >> 16
    words[offset + 1] |= mailbox & 0xffff
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
u.mem_map(0x1fc00000, 0x1000)
u.mem_map(base, 0x10000)
u.mem_map(ni, 0x10000)
u.mem_write(0x1fc00000, struct.pack('<%dI' % len(words), *words))


def put(at, value):
    u.mem_write(at, struct.pack('<I', value))


def get(at):
    return struct.unpack('<I', u.mem_read(at, 4))[0]


put(mailbox + 8, base + 0x6000)
put(mailbox + 12, 0)
put(mailbox + 16, 1)
put(ni + 0x6380, 0x80000000)
returned = []
expected_read = 0


def write_hook(uc, access, address, size, value, data):
    if address == ni + 0x6384:
        assert get(ni + 0x7160) == expected_read
        assert value == base + 0x8000 + (len(returned) % 4) * 2048 + 6
        returned.append(value)


u.hook_add(UC_HOOK_MEM_WRITE, write_hook)
pc = 0xbfc00000
for sequence in range(1000):
    read = (sequence * 8) & 1023
    expected_read = (read + 8) & 1023
    address = base + 0x8000 + (sequence % 4) * 2048
    payload = bytes((sequence + i * 7) & 255 for i in range(128))
    u.mem_write(address, payload)
    u.mem_write(base + 0x6000 + read, struct.pack('<II', address | 0x56, 76))
    put(ni + 0x7060, expected_read)
    # Every tenth packet withholds the free BID until PE has consumed EPP.
    delayed = sequence % 10 == 0
    put(ni + 0x6340, 2 if delayed else 0x80000004)
    u.emu_start(pc, 0xbfc00400, count=300)
    pc = u.reg_read(UC_MIPS_REG_PC)
    if delayed:
        assert len(returned) == sequence and get(ni + 0x7160) == expected_read
        put(ni + 0x6340, 0x80000004)
        u.emu_start(pc, 0xbfc00400, count=100)
        pc = u.reg_read(UC_MIPS_REG_PC)
    assert len(returned) == sequence + 1
    assert get(mailbox + 20) == sequence + 1
    trace = bytes(u.mem_read(mailbox + 256 + (sequence & 7) * 128, 128))
    assert trace[8:] == payload[8:] and struct.unpack('<II', trace[:8]) == (address | 0x56, 76), sequence
put(mailbox + 16, 0)
u.emu_start(pc, 0xbfc00400, count=100)
assert get(mailbox + 24) == 1 and len(returned) == 1000
record = {'assembled_production_firmware': True, 'packets': 1000,
          'EPP_wraps': 7, 'delayed_BID_credit_cases': 100,
          'payloads_exact': True, 'consume_before_buffer_return': True,
          'stop_acknowledged': True, 'hardware_test': False}
(root / 'logs/pe-rx-firmware-test.json').write_text(json.dumps(record, indent=2) + '\n')
print('Assembled PE RX: 1000 exact payloads, seven EPP wraps, 100 delayed credits, consume-before-return and stop passed')
