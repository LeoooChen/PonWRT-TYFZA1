#!/usr/bin/env python3
"""Execute interleaved Ethernet offload and raw QoS TX with one sequence owner."""
from pathlib import Path
import json
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC

directory = Path(sys.argv[1])
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
base, mailbox, pages, headers, payload, ring, bar = (
    0x04000000, 0x04002000, 0x05100000, 0x05200000, 0x06000000, 0x05000000, 0x12000000)
for address, size in [(0x1fc00000, 0x4000), (base, 0x20000), (pages, 0xc000),
                      (headers, 0xc000), (payload, 0x10000), (ring, 0x4000),
                      (bar, 0x10000), (0x10020000, 0x1000)]:
    u.mem_map(address, size)
u.mem_write(0x1fc00000, (directory / 'pe-tx.bin').read_bytes())


def put(address, value):
    u.mem_write(address, struct.pack('<I', value))


def get(address):
    return struct.unpack('<I', u.mem_read(address, 4))[0]


for index, value in [(2, bar | 0xa0000000), (3, 1), (11, ring), (12, pages),
                     (14, headers), (140, mailbox | 0xa0000000)]:
    put(mailbox + index * 4, value)
ap, client, source = [bytes.fromhex(s) for s in ('020000000001', '020000000002', '020000000003')]
u.mem_write(mailbox + 64, ap)
put(base + 0x10000 + 8 * 4, 4095)
put(base + 0x10000 + 9 * 4, 100)
sequences = {0: 4095, 1: 100}
pc = 0xbfc00400
u.reg_write(UC_MIPS_REG_A0, mailbox | 0xa0000000)
u.reg_write(UC_MIPS_REG_SP, (base + 0x1fff0) | 0xa0000000)
rows = []
for number, (encap, tid, scatter) in enumerate([(True, 0, False), (False, 0, False),
                                               (True, 0, True), (False, 1, True),
                                               (False, 0, True)]):
    body = bytes.fromhex('aaaa030000000800') + bytes(range(80))
    header = bytes.fromhex('88020000') + client + ap + source + struct.pack('<H', 777 << 4) + bytes([tid, 0])
    frame = client + source + bytes.fromhex('0800') + body[8:] if encap else header + body
    parts = [frame[:40], frame[40:]] if scatter else [frame]
    request = [0] * 32
    request[0] = number
    request[1] = number << 8 | (number + 1) << 16
    request[2] = (26 + len(body)) | 26 << 16 | len(parts) << 24
    request[3] = 4
    request[4] = 1 << 24 | (1 << 18 if encap else 1 << 22) | tid
    if not encap and '--legacy' in sys.argv:
        request[4] = 1 << 24 | 777
    request[5] = 64
    for index, part in enumerate(parts):
        address = payload + index * 0x4000
        u.mem_write(address, part)
        request[8 + index * 2], request[9 + index * 2] = address, len(part)
    u.mem_write(base + 0x4000 + number * 128, struct.pack('<32I', *request))
    put(mailbox + 8 * 4, number + 1)
    u.emu_start(pc, 0xbfc03ffc, count=50000)
    pc = u.reg_read(UC_MIPS_REG_PC)
    page = bytes(u.mem_read(pages + number * 128, 128))
    count = struct.unpack_from('<I', page, 4)[0] >> 26
    sequence = sequences[tid]
    sequences[tid] = (sequence + 1) & 4095
    assert struct.unpack_from('<I', page, 12)[0] & 4095 == sequence
    gathered = b''
    for index in range(count):
        length, address = struct.unpack_from('<HI', page, 64 + index * 6)
        gathered += bytes(u.mem_read(address, length & 2047))
    expected = header[:22] + struct.pack('<H', sequence << 4) + header[24:] + body
    assert gathered == expected, (number, gathered.hex(), expected.hex())
    for index, part in enumerate(parts):
        assert bytes(u.mem_read(payload + index * 0x4000, len(part))) == part
    rows.append({'Ethernet': encap, 'TID': tid, 'scatter': scatter, 'sequence': sequence})
print(json.dumps({'shared_sequence_wrap_and_TID_isolation': rows,
                  'headers_WD_payload_exact': True, 'source_DMA_untouched': True}, indent=2))
