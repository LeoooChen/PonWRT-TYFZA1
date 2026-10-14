#!/usr/bin/env python3
"""Execute linked PE ingestion at the old512-packet boundary and every COS wrap."""
from pathlib import Path
import struct
import subprocess
import sys
import re

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_SP, UC_MIPS_REG_RA

directory = Path(sys.argv[1])
assembly = subprocess.check_output(['llvm-objdump-18', '-d', '--no-show-raw-insn',
    str(directory / 'pe-wd.elf')], text=True)
# The target PE raises Reserved Instruction for MUL (observed EPC0x9fc027f4).
assert not re.search(r'\s(?:mul|mult|multu|div|divu)\s', assembly)
symbols = {v[2]: int(v[0], 16) for line in subprocess.check_output(
    ['llvm-nm-18', str(directory / 'pe-wd.elf')], text=True).splitlines()
    if len(v := line.split()) == 3}
base, ni, epp, packet = 0x04000000, 0x10000000, 0x07000000, 0x06000000
mailbox = base + 0x2000
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
for address, size in [(0x1fc00000, 0x4000), (base, 0x20000), (ni, 0x10000),
                       (epp, 8 * 32768), (packet, 0x1000)]:
    u.mem_map(address, size)
u.mem_write(0x1fc00000, (directory / 'pe-tx.bin').read_bytes())
def put(address, value): u.mem_write(address, struct.pack('<I', value))
def get(address): return struct.unpack('<I', u.mem_read(address, 4))[0]
put(mailbox + 25 * 4, epp)
put(mailbox + 114 * 4, 0x7060)
put(mailbox + 115 * 4, 0x7160)
put(mailbox + 167 * 4, 15)
destination = bytes.fromhex('020000000002')
u.mem_write(base + 0xe408, destination)
put(base + 0x11a04, 1)
u.mem_write(packet + 32, struct.pack('>II', 1 << 31, 68 << 15) + bytes(8))
u.mem_write(packet + 48, destination + bytes.fromhex('0200000000010800') + bytes(46))
for cos in range(8):
    for consumer, producer in [(0, 4096), (4096, 8192), (32760, 0)]:
        u.mem_write(base + 0xc400, bytes(128 * 8))
        u.mem_write(base + 0x11000, bytes(0xc00))
        u.mem_write(base + 0x13000, bytes(0x4000))
        put(base + 0x11a04, 1)
        put(mailbox + 30 * 4, 0)
        put(mailbox + 47 * 4, 0)
        put(ni + 0x659c, 1 << (24 + cos))
        put(ni + 0x7060 + cos * 4, producer)
        put(base + 0x11c00 + cos * 4, consumer)
        u.mem_write(epp + cos * 32768 + consumer, struct.pack('<II', packet | 0x50, 76))
        u.reg_write(UC_MIPS_REG_A0, mailbox | 0xa0000000)
        u.reg_write(UC_MIPS_REG_A1, base | 0xa0000000)
        u.reg_write(UC_MIPS_REG_SP, (base + 0x17ff0) | 0xa0000000)
        u.reg_write(UC_MIPS_REG_RA, 0x9fc03ff0)
        u.emu_start(symbols['ni_ingest'], 0x9fc03ff0, count=10000)
        assert get(base + 0x11c00 + cos * 4) == (consumer + 8) % 32768
        assert get(ni + 0x7160 + cos * 4) == (consumer + 8) % 32768
        assert get(mailbox + 30 * 4) == 1
        assert get(mailbox + 40 * 4) == 0
print('Linked PE EPP: all8 COS, pending beyond512 entries, separate ring DMA and32768-byte wrap passed')
