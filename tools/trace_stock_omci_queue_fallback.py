#!/usr/bin/env python3
"""Replay the shipped GEM queue fallback scan with synthetic active queue rows."""
import hashlib
import json
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import (UC_ARM64_REG_PC, UC_ARM64_REG_X0, UC_ARM64_REG_X2,
                                UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                UC_ARM64_REG_X21, UC_ARM64_REG_X22)

library = root / 'evidence/rtl-workspace/private/tyfza1/selected/ubi_r0/lib/libomci_mib.so'
records = []
cases = [
    ('SP skips a different priority', [(0, 0, 5), (1, 0, 6), (1, 0, 5)], 0x8002),
    ('SP skips WRR with the same numeric value', [(0, 0, 5), (1, 1, 5), (1, 0, 5)], 0x8002),
    ('WRR skips a different weight', [(0, 1, 9), (1, 1, 7), (1, 1, 9)], 0x8002),
    ('No equivalent target queue retains the requested queue', [(0, 0, 5), (1, 0, 6)], 0x8000),
]
for name, rows, expected in cases:
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0, 0x60000)
    uc.mem_map(0x6000000, 0x20000)
    with library.open('rb') as stream:
        for segment in ELFFile(stream).iter_segments():
            if segment['p_type'] == 'PT_LOAD':
                uc.mem_write(segment['p_vaddr'], segment.data())
    queues, info, flow = 0x6000000, 0x6010000, 0x6011000
    uc.mem_write(0x4db78, struct.pack('<Q', queues))
    uc.mem_write(info + 0x94, bytes(4))
    uc.mem_write(flow + 2, struct.pack('<H', 0x8000))
    for index, (owner, wrr, value) in enumerate(rows):
        uc.mem_write(queues + index * 32, struct.pack('<HHIH', owner, 0, wrr, value) + bytes(22))
    for register, value in ((UC_ARM64_REG_X0, info), (UC_ARM64_REG_X2, 0),
                            (UC_ARM64_REG_X3, flow), (UC_ARM64_REG_X4, len(rows)),
                            (UC_ARM64_REG_X5, 0x4db68), (UC_ARM64_REG_X21, 1),
                            (UC_ARM64_REG_X22, 0x8000)):
        uc.reg_write(register, value)
    # Run the actual scan and ME-ID selection, ending before flow programming.
    uc.emu_start(0x1e8a8, 0x1e8ec, count=1000)
    assert uc.reg_read(UC_ARM64_REG_PC) == 0x1e8ec
    selected = struct.unpack('<H', uc.mem_read(flow + 2, 2))[0]
    assert selected == expected, (name, selected, expected)
    records.append(dict(case=name, rows=rows, selected_queue=selected))

result = dict(module_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
              actual_instructions='libomci_mib.so:0x1e8a8..0x1e8ec, including branch loop at0x1eaac',
              scope='Original queue fallback scan only. Synthetic queue database, target T-CONT and requested queue; no MIB lookups, ioctl, queue programming or optical execution.',
              records=records)
(root / 'logs/stock-omci-queue-fallback.json').write_text(json.dumps(result, indent=2) + '\n')
for record in records:
    print('%s: selected=%04x' % (record['case'], record['selected_queue']))
