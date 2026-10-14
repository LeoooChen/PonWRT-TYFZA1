#!/usr/bin/env python3
"""Run assembled PE doorbells through producer wraps, pauses and fresh ACKs."""
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_PC

binary = (root / 'artifacts/live-radio-pe-doorbell/pe-tx.bin').read_bytes()
words = list(struct.unpack('<%dI' % (len(binary) // 4), binary))
base, registers = 0x04000000, 0x12000000
mailbox = base + 0x800
for offset in (0, 0x380 // 4):
    words[offset] |= (mailbox | 0xa0000000) >> 16
    words[offset + 1] |= mailbox & 65535
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
u.mem_map(0x1fc00000, 4096)
u.mem_map(base, 65536)
u.mem_map(registers, 65536)
u.mem_write(0x1fc00000, struct.pack('<%dI' % len(words), *words))


def put(at, value):
    u.mem_write(at, struct.pack('<I', value))


def get(at):
    return struct.unpack('<I', u.mem_read(at, 4))[0]


indices = [0x1058, 0x105c, 0x1060, 0x1064, 0x1078, 0x107c]
put(mailbox + 8, 0xb2000000)
for ring, index in enumerate(indices):
    put(mailbox + 96 + ring * 4, index)
writes = []


def write_hook(uc, access, address, size, value, data):
    if registers <= address < registers + 65536:
        assert size == 2 and get(mailbox + 12) & 1
        writes.append((address - registers, value))


u.hook_add(UC_HOOK_MEM_WRITE, write_hook)
pc = 0xbfc00000


def run():
    global pc
    u.emu_start(pc, 0xbfc00400, count=1000)
    pc = u.reg_read(UC_MIPS_REG_PC)


run()
assert get(mailbox) == 0x50455458 and not writes
command = 0
for sequence in range(1024):
    command += 2
    put(mailbox + 12, command | 1)
    for ring in range(6):
        put(mailbox + 32 + ring * 4, (sequence + 1 + ring) & 255)
    run()
    for ring in range(6):
        assert get(mailbox + 64 + ring * 4) == (sequence + 1 + ring) & 255
    command += 2
    put(mailbox + 12, command)
    previous = len(writes)
    run()
    assert get(mailbox + 16) == command and len(writes) == previous
    # Pending producers survive pause and are published only after resume.
    put(mailbox + 32, (sequence + 2) & 255)
    run()
    assert len(writes) == previous
assert get(mailbox + 4) == len(writes)
print(f'Assembled PE TX: {len(writes)} actual halfword doorbells, six rings, 1024 fresh pause ACKs and producer wraps passed')
