#!/usr/bin/env python3
"""Count real MIPS downlink work after prompt RPQ completion, without a device."""
import json
from pathlib import Path
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2
from unicorn.mips_const import UC_MIPS_REG_RA, UC_MIPS_REG_SP, UC_MIPS_REG_V0

BASE, NI, PAYLOAD = 0x04000000, 0x10000000, 0x06000000
MAILBOX, REQUEST = BASE + 0x2000, BASE + 0x7000


def profile(directory):
    directory = Path(directory)
    entries = subprocess.check_output(['llvm-nm-18', str(directory / 'pe-wd.elf')], text=True)
    symbols = {p[2]: int(p[0], 16) for line in entries.splitlines() if len(p := line.split()) == 3}
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for address, size in [(0x1fc00000, 0x4000), (BASE, 0x20000), (NI, 0x10000), (PAYLOAD, 0x10000)]:
        u.mem_map(address, size)
    u.mem_write(0x1fc00000, (directory / 'pe-tx.bin').read_bytes())
    put = lambda address, value: u.mem_write(address, struct.pack('<I', value))
    get = lambda address: struct.unpack('<I', u.mem_read(address, 4))[0]
    put(MAILBOX + 25 * 4, BASE + 0x18000)
    put(MAILBOX + 112 * 4, 1)
    put(MAILBOX + 114 * 4, 0x7060)
    put(MAILBOX + 115 * 4, 0x7160)
    put(MAILBOX + 167 * 4, 12)
    put(BASE + 0x11a00 + 2 * 4, 1)
    put(BASE + 0xe200 + 2 * 4, 3839 << 18 | 1 << 17)
    put(BASE + 0x12000 + 2 * 8 * 4, 64)
    mac = bytes.fromhex('02000000004e')
    u.mem_write(BASE + 0xe400 + 2 * 8, mac)
    u.mem_write(BASE + 0xd000 + 2 * 32, struct.pack('<8I',
                0, 0, 26 << 16, 0x00020100, 2 << 24 | 1 << 18 | 1 << 16,
                3 << 16 | 64, 0, 0))
    work = {}

    def read(uc, access, address, size, value, data):
        address &= 0x1fffffff
        if BASE <= address < BASE + 0x20000:
            work['DDR_reads'] += 1
        if BASE + 0x11000 <= address < BASE + 0x11800:
            work['waiting_reads'] += 1
            work['waiting_slots'].add((address - BASE - 0x11000) // 16)
        if NI <= address < NI + 0x10000:
            work['NI_MMIO_reads'] += 1

    def write(uc, access, address, size, value, data):
        if BASE <= (address & 0x1fffffff) < BASE + 0x20000:
            work['DDR_writes'] += 1

    def instruction(uc, address, size, data):
        work['instructions'] += 1

    u.hook_add(UC_HOOK_MEM_READ, read)
    u.hook_add(UC_HOOK_MEM_WRITE, write)
    u.hook_add(UC_HOOK_CODE, instruction)

    def call(name):
        nonlocal work
        work = dict(instructions=0, DDR_reads=0, DDR_writes=0, NI_MMIO_reads=0,
                    waiting_reads=0, waiting_slots=set())
        u.reg_write(UC_MIPS_REG_A0, MAILBOX | 0xa0000000)
        u.reg_write(UC_MIPS_REG_A1, BASE | 0xa0000000)
        u.reg_write(UC_MIPS_REG_A2, REQUEST | 0xa0000000)
        u.reg_write(UC_MIPS_REG_SP, (BASE + 0x17ff0) | 0xa0000000)
        u.reg_write(UC_MIPS_REG_RA, 0x9fc01ff0)
        u.emu_start(symbols[name], 0x9fc01ff0, count=100000)
        result = u.reg_read(UC_MIPS_REG_V0)
        work['waiting_slots'] = len(work['waiting_slots'])
        return result, work.copy()

    rounds, held = [], []
    for batch in range(3):
        for n in range(2):
            frame = mac + bytes.fromhex('0200000000650800') + bytes(1500)
            address = PAYLOAD + n * 2048
            u.mem_write(address, bytes(32) + struct.pack('>II', 0x80000000, (1514 + 8) << 15) + bytes(8) + frame)
            u.mem_write(BASE + 0x18000 + (batch * 2 + n) * 8,
                        struct.pack('<II', address | 0x50, 1530 | 1 << 14))
        put(NI + 0x7060, (batch + 1) * 16)
        ingest = [call('ni_ingest'), call('ni_ingest')]
        frames, select = call('ni_select')
        # The real main loop ignores ni_ingest's return, so LLVM elides it.
        assert get(NI + 0x7160) == (batch + 1) * 16 and frames == 2
        request = struct.unpack('<12I', u.mem_read(REQUEST, 48))
        assert request[8:12] == (PAYLOAD + 48, 1514, PAYLOAD + 2048 + 48, 1514)
        first = request[0] - 256
        second = get(BASE + 0xc404 + first * 8) >> 24 & 127
        assert first != second and not set([first, second]).intersection(held)
        assert get(MAILBOX + 30 * 4) == len(held) + 2
        rounds.append(dict(ingest=[w for _, w in ingest], select=select,
                           allocated_ids=[first, second], previous_inflight_ids=held.copy(), frames=frames))
        if batch == 0:
            held = [first, second]
            continue
        # Complete only the named BIDs; the second pair was allocated with the first held.
        for owner in held + [first, second]:
            put(BASE + 0xc400 + owner * 8, 0)
            put(BASE + 0xc404 + owner * 8, 0)
        put(MAILBOX + 30 * 4, 0)
        held = []
    return dict(binary=str(directory / 'pe-tx.bin'), rounds=rounds)


print(json.dumps(dict(scope='Actual linked ni_ingest/ni_select: six frames on one TID, cursor after an earlier pair, one delayed completion pair. No DDR latency, radio, PCIe or throughput model.',
                      before=profile(sys.argv[1]), after=profile(sys.argv[2])), indent=2))
