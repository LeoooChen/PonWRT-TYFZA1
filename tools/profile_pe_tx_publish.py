#!/usr/bin/env python3
"""Compare real PE TX instructions and published DMA bytes, not RF throughput."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import test_pe_downlink_gather as fixture
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_SP


def profile(directory, lengths):
    fixture.binary = (Path(directory) / 'pe-tx.bin').read_bytes()
    instructions = struct.unpack('<%dI' % (len(fixture.binary) // 4), fixture.binary)
    counts, published = Counter(), []

    def observe(u):
        # Production pe_probe_start selects the cached private stack alias.
        u.reg_write(UC_MIPS_REG_SP, 0x8401fff0)

        def instruction(uc, pc, size, user):
            if published:
                return
            counts['instructions'] += 1
            word = instructions[((pc & 0x1fffffff) - 0x1fc00000) // 4]
            opcode = word >> 26
            if opcode not in (32, 33, 34, 35, 36, 37, 38, 40, 41, 42, 43, 46):
                return
            offset = word & 65535
            if offset & 32768:
                offset -= 65536
            address = (uc.reg_read(UC_MIPS_REG_0 + ((word >> 21) & 31)) + offset) & 0x1fffffff
            operation = 'read' if opcode < 40 else 'write'
            region = 'other'
            for start, end, name in (
                (0x0401c000, 0x04020000, 'private_stack'),
                (0x04000000, 0x0401c000, 'shared_state'),
                (0x05100000, 0x0510c000, 'WD'),
                (0x05200000, 0x0520c000, 'header'),
                (0x06000000, 0x06010000, 'BID'),
                (0x12000000, 0x12010000, 'PCIe'),
                (0x10000000, 0x10010000, 'NI'),
            ):
                if start <= address < end:
                    region = name
                    break
            counts[region + '_' + operation] += 1
            if address == 0x12001058 and operation == 'write':
                txbd = bytes(uc.mem_read(0x05000000, 8))
                used, page = struct.unpack('<II', txbd)
                published.append(txbd + bytes(uc.mem_read(page, used & 0xffff)))

        u.hook_add(UC_HOOK_CODE, instruction)

    fixture.case(lengths, 7935, True, bandwidth=3, rate=0x19b, observe=observe)
    assert len(published) == 1
    return dict(PE_sha256=hashlib.sha256(fixture.binary).hexdigest(),
                first_MPDU_work=dict(counts), DMA_image_sha256=hashlib.sha256(published[0]).hexdigest())


rows = []
for lengths in ([1514], [1514] * 5, [64, 65, 66, 67] * 2):
    before, after = [profile(directory, lengths) for directory in sys.argv[1:3]]
    assert before['DMA_image_sha256'] == after['DMA_image_sha256']
    assert not after['first_MPDU_work'].get('header_read', 0)
    assert not after['first_MPDU_work'].get('header_write', 0)
    assert after['first_MPDU_work']['WD_write'] < before['first_MPDU_work']['WD_write']
    rows.append(dict(MSDU_bytes=lengths, before=before, after=after))
print(json.dumps(dict(scope='Three linked PE cases including exact payload/padding and RPQ/EQ ownership checks. '
                     'Instruction counts stop at the first TX doorbell; LWL/LWR/SWL/SWR count individually. '
                     'No DDR/PCIe/cache latency or on-air throughput model.', cases=rows), indent=2))
