#!/usr/bin/env python3
"""Replay original post-delete queue ownership for last and shared GEM references."""
import hashlib
import json
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_X27, UC_ARM64_REG_X28

library = root / 'evidence/rtl-workspace/private/tyfza1/selected/ubi_r0/lib/libomci_mib.so'
records = []
for remaining in (0, 1):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0, 0x60000)
    uc.mem_map(0x6000000, 0x10000)
    with library.open('rb') as stream:
        for segment in ELFFile(stream).iter_segments():
            if segment['p_type'] == 'PT_LOAD':
                uc.mem_write(segment['p_vaddr'], segment.data())
    queues, index = 0x6000000, 3
    uc.mem_write(0x4db78, struct.pack('<Q', queues))
    row = bytearray(32)
    struct.pack_into('<HHIH', row, 0, 0, 4 if remaining else 0xffff, 1, 9)
    struct.pack_into('<I', row, 28, remaining)
    uc.mem_write(queues + index * 32, bytes(row))
    uc.reg_write(UC_ARM64_REG_X27, 0x4db68)
    uc.reg_write(UC_ARM64_REG_X28, index * 32)
    # The original ioctl and queue cleanup succeeded before this basic block.
    uc.emu_start(0x1ed70, 0x1ea88, count=100)
    assert uc.reg_read(UC_ARM64_REG_PC) == 0x1ea88
    actual = bytes(uc.mem_read(queues + index * 32, 32))
    owner = struct.unpack_from('<H', actual)[0]
    assert owner == (0 if remaining else 0xffff)
    if remaining:
        assert actual == row
    else:
        assert actual[4:13] == bytes(9)
    records.append(dict(remaining_GEM_references=remaining, queue_owner=owner,
                        scheduling_metadata_cleared=actual[4:13] == bytes(9)))

result = dict(module_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
              actual_instructions='libomci_mib.so:omci_wrapper_cfgGemFlow+0x6d0,0x1ed70..0x1ed90',
              scope='Actual post-delete reference test and queue ownership/metadata writes. Synthetic queue database; preceding ioctl/queue-removal success is a precondition, not executed here. No physical PUC operation or full deletion lifecycle proof.',
              records=records)
(root / 'logs/stock-omci-queue-release.json').write_text(json.dumps(result, indent=2) + '\n')
for record in records:
    print('Remaining GEM references=%u queue owner=%04x' %
          (record['remaining_GEM_references'], record['queue_owner']))
