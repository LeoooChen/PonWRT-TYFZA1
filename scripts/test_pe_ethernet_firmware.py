#!/usr/bin/env python3
"""Execute actual PE Ethernet conversion, gather DMA and per-TID sequence state."""
import json
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_PC

binary = (Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'artifacts/live-radio-pe-eth/pe-tx.bin').read_bytes()
words = list(struct.unpack('<%dI' % (len(binary) // 4), binary))
# The MIPS32 emulator has no Realtek CP3 instruction-memory controller.
for i, word in enumerate(words):
    if word in (0x4c880000, 0x4c880800, 0x4cc80000, 0x4cc81000): words[i] = 0
    if word == 0x4c084000: words[i] = 0x24080000
base, mailbox, ring, pages, headers, payload, bar = 0x04000000, 0x04002000, 0x05000000, 0x05100000, 0x05200000, 0x06000000, 0x12000000
words[0] |= (mailbox | 0xa0000000) >> 16
words[1] |= mailbox & 65535
words[2] |= (base | 0xa0000000) >> 16
words[0x380 // 4] |= (mailbox | 0xa0000000) >> 16
words[0x384 // 4] |= mailbox & 65535
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
for at, count in ((0x1fc00000, (len(binary) + 4095) & ~4095), (base, 65536), (ring, 16384), (pages, 32768), (headers, 32768), (payload, 65536), (bar, 65536)):
    u.mem_map(at, count)
u.mem_write(0x1fc00000, struct.pack('<%dI' % len(words), *words))
u.mem_map(0x10020000, 4096)


def put(at, value): u.mem_write(at, struct.pack('<I', value))
def get(at): return struct.unpack('<I', u.mem_read(at, 4))[0]


put(mailbox + 8, 0xb2000000)
put(mailbox + 44, ring)
put(mailbox + 48, pages)
put(mailbox + 56, headers)
ap = bytes.fromhex('020000000001')
u.mem_write(mailbox + 64, ap)
pc = 0xbfc00000
transmitted = []


def observe(uc, access, at, size, value, data):
    if bar <= at < bar + 65536:
        assert size == 2
        transmitted.append(value)


u.hook_add(UC_HOOK_MEM_WRITE, observe, begin=bar, end=bar + 65535)
sequences = {}
for number in range(512):
    macid, tid = 1 + (number % 3), number % 8
    header_bytes = 26 if number & 1 else 24
    protected = bool(number & 2)
    dest = bytes([2, 3, 4, 5, 6, macid])
    source = bytes.fromhex('10aabbccddee')
    body = bytes((number + n) & 255 for n in range(4096))
    ethernet = dest + source + bytes.fromhex('0800') + body
    parts = [ethernet] if number % 3 else [ethernet[:80], ethernet[80:2070], ethernet[2070:]]
    r = [0] * 32
    r[0] = number & 255
    r[1] = ((number & 255) << 8) | (((number + 1) & 255) << 16)
    r[2] = len(body) + header_bytes + 8 | header_bytes << 16 | len(parts) << 24
    r[3] = 0x8f | 1 << 12
    r[4] = macid << 24 | 1 << 18 | (1 << 16 if protected else 0) | (1 << 12 if tid in (2, 3, 5, 7) else 0) | tid
    r[5] = 32 | 4 << 8 | 7 << 16
    for n, part in enumerate(parts):
        at = payload + n * 16384
        u.mem_write(at, part)
        r[8 + n * 2], r[9 + n * 2] = at, len(part)
    u.mem_write(base + 0x4000 + (number & 255) * 128, struct.pack('<32I', *r))
    put(mailbox + 32, (number + 1) & 255)
    put(mailbox + 12, 1)
    u.emu_start(pc, 0xbfc03000, count=6000)
    pc = u.reg_read(UC_MIPS_REG_PC)
    assert get(mailbox + 36) == (number + 1) & 255
    sequence = sequences.get((macid, tid), 0)
    sequences[macid, tid] = (sequence + 1) & 4095
    header = bytes([0x88 if header_bytes == 26 else 8, 0x42 if protected else 2, 0, 0]) + dest + ap + source + struct.pack('<H', sequence << 4)
    if header_bytes == 26: header += bytes([tid, 0])
    header += bytes.fromhex('aaaa030000000800')
    assert bytes(u.mem_read(headers + r[0] * 128, len(header))) == header
    page = bytes(u.mem_read(pages + r[0] * 128, 128))
    count = struct.unpack_from('<I', page, 4)[0] >> 26
    gathered = b''
    final = []
    for entry in range(count):
        length, address = struct.unpack_from('<HI', page, 64 + entry * 6)
        gathered += bytes(u.mem_read(address, length & 2047))
        final.append(bool(length & 0x8000))
    assert gathered == header + body and final == [False] * (count - 1) + [True]
    assert len(transmitted) == number + 1
    assert get(base + 0x3000 + (macid * 8 + tid) * 4) == sequences[macid, tid]
    # PE reads the Ethernet buffers; neither address nor payload is overwritten.
    for n, part in enumerate(parts): assert bytes(u.mem_read(payload + n * 16384, len(part))) == part
record = {'actual_linked_PE_firmware': True, 'Ethernet_frames': 512,
          'QOS_and_legacy_headers_exact': True, 'protected_and_plain_frames': True,
          'per_MACID_TID_sequences': True, 'single_and_scatter_DMA_payload_exact': True,
          'input_Ethernet_unchanged': True, 'hardware_test': False}
(root / 'logs/pe-ethernet-firmware-test.json').write_text(json.dumps(record, indent=2) + '\n')
print('Linked PE Ethernet firmware: 512 exact QoS/legacy headers and gathered payloads, per-peer/TID sequences, input buffers untouched')
