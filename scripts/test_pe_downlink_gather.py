#!/usr/bin/env python3
"""Execute the linked PE downlink and verify multi-MSDU DMA and RPQ ownership."""
from pathlib import Path
import json
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC

directory = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'artifacts/pe-radio-production'
binary = (directory / 'pe-tx.bin').read_bytes()


def case(lengths, cap, from_epp=False, hw_rts=False, bandwidth=2, rate=0x100, observe=None):
    base, mb, pages, headers, payload, ring, bar, ni, reports = (
        0x04000000, 0x04002000, 0x05100000, 0x05200000, 0x06000000,
        0x05000000, 0x12000000, 0x10000000, 0x06200000)
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, size in [(0x1fc00000, 0x4000), (base, 0x20000), (pages, 0xc000),
                     (headers, 0xc000), (payload, 0x10000), (ring, 0x4000),
                     (bar, 0x10000), (ni, 0x10000), (reports, 0x80000),
                     (0x10020000, 0x1000), (0x10030000, 0x1000)]:
        u.mem_map(at, size)
    u.mem_write(0x1fc00000, binary)
    put = lambda at, value: u.mem_write(at, struct.pack('<I', value))
    get = lambda at: struct.unpack('<I', u.mem_read(at, 4))[0]
    ap, dst = bytes.fromhex('020000000001'), bytes.fromhex('020000000002')
    u.mem_write(mb + 64, ap)
    for index, value in [(0, 0x50455744), (2, bar | 0xa0000000), (3, 1),
                         (11, ring), (12, pages), (14, headers), (24, 1),
                         (30, 0), (38, len(lengths)), (116, 0x6380),
                         (117, 6), (140, mb | 0xa0000000)]:
        put(mb + index * 4, value)
    peer = [0, 0, 26 << 16, rate | bandwidth << 16 | (1 << 22 if hw_rts else 0),
            1 << 24 | 1 << 18 | 1 << 16, 7 << 16 | 32, 0, 0]
    u.mem_write(base + 0xd020, struct.pack('<8I', *peer))
    put(base + 0xe204, cap << 18)
    put(base + 0x11a04, 1)
    put(base + 0x11804, len(lengths))
    put(base + 0x13020, len(lengths))
    put(base + 0x18020, len(lengths))
    put(base + 0x15020, payload)
    put(base + 0x16020, payload + (len(lengths) - 1) * 2048)
    put(mb + 170 * 4, 2)
    put(base + 0x19004, 2)
    put(base + 0x19204, 2)
    if from_epp:
        put(mb + 25 * 4, 0x10030000)
        put(mb + 114 * 4, 0x7060)
        put(mb + 115 * 4, 0x7160)
        put(mb + 167 * 4, 12)
        put(mb + 30 * 4, 0)
        put(mb + 38 * 4, 0)
        put(base + 0x11804, 0)
        put(base + 0x13020, 0)
        put(base + 0x15020, 0)
        put(base + 0x16020, 0)
        put(base + 0x18020, 0)
        put(mb + 170 * 4, 0)
        u.mem_write(base + 0xe408, dst)
        put(ni + 0x659c, 1 << 24)
        put(ni + 0x7060, len(lengths) * 8)
    frames = []
    for n, length in enumerate(lengths):
        frame = dst + bytes([2, 3, 4, 5, 6, n]) + bytes.fromhex('0800')
        frame += bytes([n + 1]) * (length - 14)
        frames.append(frame)
        at = payload + n * 2048
        u.mem_write(at + 48, frame)
        if from_epp:
            u.mem_write(at + 32, struct.pack('>II', 1 << 31, (length + 8) << 15) + bytes(8))
            u.mem_write(0x10030000 + n * 8, struct.pack('<II', at | 0x50, length + 16))
        else:
            u.mem_write(at, struct.pack('<4I', at + 48, length, 1,
                        at + 2048 if n + 1 < len(lengths) else 0))
    submits, returns = [], []
    u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
               submits.append(value), begin=bar + 0x1058, end=bar + 0x1059)
    u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
               returns.append(value), begin=ni + 0x6384, end=ni + 0x6387)
    u.reg_write(UC_MIPS_REG_A0, mb | 0xa0000000)
    u.reg_write(UC_MIPS_REG_SP, (base + 0x1fff0) | 0xa0000000)
    if observe:
        observe(u)
    u.emu_start(0xbfc00400, 0xbfc03ffc, count=100000)
    assert get(mb + 28 * 4) == len(lengths) and get(mb + 38 * 4) == 0
    assert get(mb + 30 * 4) == len(submits) and not returns
    assert get(base + 0x18020) == len(lengths)
    member = 0
    all_ids = []
    for slot in range(len(submits)):
        page = bytes(u.mem_read(pages + (256 + slot) * 128, 128))
        count = struct.unpack_from('<I', page, 4)[0] >> 26
        assert bool(struct.unpack_from('<I', page, 32)[0] & (1 << 12)) == hw_rts
        assert struct.unpack_from('<I', page, 28)[0] >> 28 & 3 == bandwidth
        assert struct.unpack_from('<I', page, 28)[0] >> 16 & 0x1ff == rate
        expected = bytes.fromhex('88420000') + dst + ap
        expected += (ap if count > 1 else frames[member][6:12]) + struct.pack('<H', slot << 4)
        expected += bytes([128 if count > 1 else 0, 0])
        ids = []
        for n in range(count):
            frame = frames[member + n]
            if count > 1:
                if n: expected += bytes((-len(expected) + 26) & 3)
                expected += frame[:12] + struct.pack('>H', len(frame) - 6)
            expected += bytes.fromhex('aaaa030000000800') + frame[14:]
            ids.append(member + n)
        gathered = b''
        for n in range(count):
            length, address = struct.unpack_from('<HI', page, 64 + n * 6)
            length, last = length & 2047, bool(length & 0x8000)
            assert last == (n + 1 == count)
            assert payload + (member + n) * 2048 <= address
            assert address + length <= payload + (member + n + 1) * 2048
            gathered += bytes(u.mem_read(address, length))
        assert gathered == expected, (lengths, count, len(gathered), len(expected))
        assert struct.unpack_from('<I', page, 8)[0] & 0x3fff == len(gathered)
        assert len(gathered) - 26 <= cap or count == 1
        chain = get(base + 0xc400 + slot * 8)
        for n in range(count):
            assert chain == payload + (member + n) * 2048
            chain = get(chain)
        assert chain == 0
        all_ids.append(ids)
        member += count
    assert member == len(lengths)
    put(mb + 68 * 4, reports)
    put(mb + 72 * 4, reports + 0x40000)
    for slot, ids in enumerate(all_ids):
        put(reports + slot * 1024, 24 | 0xc000 | (slot + 1) << 16)
        put(reports + slot * 1024 + 4, 7 << 24 | 4)
        put(reports + slot * 1024 + 20, (256 + slot) << 16 | 1)
    put(bar + 0x121c, len(all_ids) << 16)
    u.emu_start(u.reg_read(UC_MIPS_REG_PC), 0xbfc03ffc, count=100000)
    assert get(mb + 45 * 4) == len(submits) and not returns
    put(ni + 0x6340, 0x8000007c)
    put(ni + 0x6380, 0x80000000)
    u.emu_start(u.reg_read(UC_MIPS_REG_PC), 0xbfc03ffc, count=100000)
    assert returns == [payload + n * 2048 | 6 for n in range(len(lengths))]
    assert get(mb + 30 * 4) == 0 and get(mb + 29 * 4) == len(lengths)
    assert get(base + 0x18020) == 0
    for n, frame in enumerate(frames):
        assert bytes(u.mem_read(payload + n * 2048 + 62, len(frame) - 14)) == frame[14:]
        assert get(base + 0xc400 + n * 8) == 0
    return {'lengths': lengths, 'cap': cap, 'MPDUs': len(submits), 'from_EPP': from_epp, 'HW_RTS': hw_rts, 'bandwidth': bandwidth, 'rate': rate}


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[2] == '--160':
        rows = [case([1514] * 4, 7935, True, bandwidth=3, rate=rate) for rate in (0x119, 0x19b)]
    else:
        rows = [case([64 + (n + pad) % 4 for n in range(count)], 7935)
                for count in range(2, 9) for pad in range(4)]
        rows += [case([1500] * 8, 7935), case([1500] * 8, 3839)]
        rows += [case([1514] * 8, 7935, True), case([64, 65, 66, 67], 7935, True)]
        rows += [case([1514] * 4, 7935, True, True)]
    print(json.dumps({'cases': rows, 'exact_headers_payload_padding_DMA': True,
                      'one_WP_per_aggregate': True,
                      'ownership_until_RPQ_and_EQ_credit': True}, indent=2))
