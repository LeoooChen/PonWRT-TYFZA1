#!/usr/bin/env python3
"""Execute the linked PE downlink and verify multi-MSDU DMA and RPQ ownership."""
from pathlib import Path
import json
from pe_tx_ownership_fixture import initialize, map_report
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC

directory = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'artifacts/pe-radio-production'
binary = (directory / 'pe-tx.bin').read_bytes()


def case(lengths, cap, from_epp=False, hw_rts=False, bandwidth=2, rate=0x100, observe=None, ni_header=16, compact_epp=False, client=False, multicast=False, noack=False, preserve_group=False, preload=True, policy=None, source=0, prepare_prefix=True, encrypted=True, gcmp=False, extended=False, role=0, self_address=None, wifi_id=1):
    if from_epp and not multicast: ni_header = 16
    base, mb, pages, headers, payload, ring, bar, ni, reports = (
        0x04000000, 0x04002000, 0x05100000, 0x05400000, 0x06000000,
        0x05000000, 0x12000000, 0x10000000, 0x06200000)
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, size in [(0x1fc00000, 0x10000), (base, 0x80000), (pages, 0x32000),
                     (headers, 0xc000), (payload, 0x10000), (ring, 0x4000),
                     (bar, 0x10000), (ni, 0x10000), (reports, 0x80000),
                     (0x10020000, 0x1000), (0x10030000, 0x1000), (0x07e00000, 0x4000)]:
        u.mem_map(at, size)
    if multicast:
        assert from_epp
        table=0x08000000;u.mem_map(table,0x7000)
        u.mem_write(table,struct.pack('<II',7,2))
    u.mem_write(0x1fc00000, binary)
    initialize(u, base)
    put = lambda at, value: u.mem_write(at, struct.pack('<I', value))
    get = lambda at: struct.unpack('<I', u.mem_read(at, 4))[0]
    ap, dst = bytes.fromhex('020000000001'), bytes.fromhex('020000000002')
    if preserve_group:
        assert client and multicast
        dst=bytes.fromhex('01005e010203')
    bssid = bytes.fromhex('020000009999')
    if multicast:
        u.mem_write(table+16+96*9,struct.pack('<II',7,1|source<<16)+dst+bytes([1 if noack else 0,1])+bytes(64)+(bssid if client else dst)+bytes(2)+struct.pack('<II',((7<<16)|(1<<27 if gcmp else 0)|(1<<28 if extended else 0) if encrypted else 0),(1<<16 if encrypted else 0)|(1<<18)|(1<<23 if client else 0)))
        put(mb+210*4,table)
    u.mem_write(mb + 64, ap)
    if self_address is not None:
        ap=bytes.fromhex(self_address);u.mem_write(base+0x68408,ap+bytes(2))
    u.mem_write(base + 0xe408, bssid if client else dst)
    for index, value in [(0, 0x50455744), (2, bar | 0xa0000000), (3, 1),
                         (11, ring), (12, pages), (14, headers), (24, 1),
                         (30, 0), (38, len(lengths)), (116, 0x6380),
                         (117, 6), (140, mb | 0xa0000000), (142, 1)]:
        put(mb + index * 4, value)
    peer = [0, 0, 26 << 16, rate | bandwidth << 16 | (1 << 22 if hw_rts else 0) | role<<24 | ((1<<23) if self_address is not None else 0),
            1 << 24 | 1 << 18 | (1 << 16 if encrypted else 0) | (1 << 23 if client else 0) , ((7 << 16)|(1<<27 if gcmp else 0)|(1<<28 if extended else 0) if encrypted else 0) | 32 | (1 << 15 if preload else 0), 0, 0]
    u.mem_write(base + 0xd020, struct.pack('<8I', *peer))
    put(base + 0xe204, cap << 18)
    put(base + 0x12020, 32)  # negotiated BA limit, as consumed by ni_select
    effective_policy = policy if policy is not None else 3793 if cap == 3839 else 7433 if cap == 7935 else 10896
    put(base + 0x39004, effective_policy | (0 if not effective_policy else 2 if effective_policy <= 3793 else 4 if effective_policy <= 7433 else 7) << 16)
    put(base + 0x11a04, wifi_id)
    put(base + 0x11804, len(lengths))
    put(base + 0x13020, len(lengths))
    put(base + 0x18020, len(lengths))
    put(base + 0x3a080, sum(lengths))
    put(base + 0x3a088, 1)
    put(base + 0x15020, payload)
    put(base + 0x19604, 1)
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
        put(base + 0x3a080, 0)
        put(mb + 170 * 4, 0)
        put(ni + 0x659c, 1 << 24)
        put(ni + 0x7060, len(lengths) * 8)
    frames = []
    for n, length in enumerate(lengths):
        frame = dst + bytes([2, 3, 4, 5, 6, n]) + bytes.fromhex('0800')
        frame += bytes([n + 1]) * (length - 14)
        # Real QoS classification must see DSCP0, not patterned payload as TOS.
        if len(frame) >= 16: frame = frame[:14] + b'\x45\x00' + frame[16:]
        frames.append(frame)
        at = payload + n * 2048
        u.mem_write(at + 64 + ni_header, frame)
        if from_epp:
            if multicast:u.mem_write(at+64+ni_header,bytes.fromhex('01005e010203'))
            u.mem_write(at + 64, struct.pack('>II', ((1 << 31) if ni_header == 16 else 0)|(265<<18 if multicast else 0), (length + ni_header - 8) << 15 | source<<9 | (19<<3 if multicast else 0)) + bytes(ni_header - 8))
            u.mem_write(0x10030000 + n * 8, struct.pack('<II', at | 0x50, (wifi_id << 14 if not multicast or compact_epp else 0) | (length + 16)))
        else:
            # This fixture bypasses ni_ingest. Prepare the same large-frame
            # prefix and metadata flag; real EPP cases exercise admission.
            if prepare_prefix and length > 1000:
                u.mem_write(at + 64 + ni_header - 8, frame[:12] +
                            struct.pack('>H', length - 6) + bytes.fromhex('aaaa03000000'))
            u.mem_write(at, struct.pack('<4I', at + 2048 if n + 1 < len(lengths) else 0,
                        length | ni_header << 16 | (1 << 24 if prepare_prefix and length > 1000 else 0), 0xdeadbeef, 0xa5a5a5a5))
    submits, returns = [], []
    u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
               submits.extend(range(submits[-1] + 1 if submits else 1, value + 1)),
               begin=bar + 0x1058, end=bar + 0x1059)
    u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
               returns.append(value), begin=ni + 0x6384, end=ni + 0x6387)
    u.reg_write(UC_MIPS_REG_A0, mb | 0xa0000000)
    u.reg_write(UC_MIPS_REG_SP, 0xa7e01ff0)
    if observe:
        observe(u)
    u.emu_start(0xbfc00400, 0x9fc0fff0, count=100000)
    # A cap-limited dequeue rearms the stock ordinary queue timer. Advance
    # actual TSF for each remaining selection instead of assuming one expiry.
    for interval in range(1, len(lengths) + 1):
        if not get(mb + 38 * 4): break
        put(bar + 0xc438, interval * 10001)
        u.emu_start(u.reg_read(UC_MIPS_REG_PC), 0x9fc0fff0, count=100000)
    assert get(mb + 28 * 4) == len(lengths) and get(mb + 38 * 4) == 0
    assert get(base + 0x1c020) == len(lengths)
    assert get(mb + 30 * 4) == len(submits) and not returns
    assert get(base + 0x18020) == len(lengths)
    if source == 6:
        assert get(base + 0x1e808) == len(lengths)
        for n in range(len(lengths)):
            assert get(payload + n * 2048 + 20) == 2
            assert get(payload + n * 2048 + 24) == 0
    member = 0
    all_ids = []
    previous_length = 0
    for slot in range(len(submits)):
        page = bytes(u.mem_read(get(ring + slot * 8 + 4), 128))
        count = struct.unpack_from('<I', page, 4)[0] >> 26
        assert struct.unpack_from('<I', page, 48)[0] & 0x88000000 == (0x88000000 if hw_rts else 0)
        assert struct.unpack_from('<I', page, 28)[0] >> 28 & 3 == bandwidth
        assert struct.unpack_from('<I', page, 28)[0] >> 16 & 0x1ff == rate
        expected = bytes.fromhex(('88410000' if client else '88420000') if encrypted else ('88010000' if client else '88020000')) + (bssid if client else dst) + (frames[member][6:12] if client else ap)
        expected += ((bssid if count > 1 else dst) if client else
                     (ap if count > 1 else frames[member][6:12])) + struct.pack('<H', slot << 4)
        expected += bytes([(128 if count > 1 else 0)|(32 if noack else 0), 0])
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
        assert struct.unpack_from('<I', page, 32)[0] & 127 == role
        assert struct.unpack_from('<I', page, 8)[0] & 0x3fff == len(gathered)
        assert struct.unpack_from('<I', page, 4)[0]&15 == (((8 if gcmp else 6)+extended) if encrypted else 0)
        assert bool(struct.unpack_from('<I', page, 0)[0] & (1 << 6)) == encrypted, 'incorrect hardware PN'
        assert bool(struct.unpack_from('<I', page, 0)[0] & (1 << 24)) == encrypted, 'incorrect security enable'
        if noack:
            assert struct.unpack_from('<I', page, 0)[0] & (1 << 31)
            assert not(struct.unpack_from('<I', page, 12)[0] & (1 << 12)), 'no-ACK enabled BA'
        assert struct.unpack_from('<I', page, 40)[0] & 0x17f == (0x107 if encrypted else 0), 'wrong CCMP CAM'
        assert bool(get(base + 0x20004 + slot * 8) & (1 << 15)) == encrypted, 'incorrect key ownership'
        assert len(gathered) - 26 <= (cap if policy is None else min(cap,policy)) or count == 1
        # Only preloaded peers may cross descending lengths without BK.
        boundary = noack or slot == 0 or (not preload and len(gathered) < previous_length)
        assert struct.unpack_from('<I', page, 12)[0] & 0x3000 == ((0x2000 if boundary else 0) | (0 if noack else 0x1000)), (lengths, slot, page[:16].hex())
        previous_length = len(gathered)
        chain = get(base + 0x20000 + slot * 8)
        for n in range(count):
            assert chain == payload + (member + n) * 2048
            chain = get(chain)
        assert chain == 0
        all_ids.append(ids)
        member += count
    assert member == len(lengths)
    # Seed completed WPs to isolate PE1 gather and BID recycling.
    # The RPQ firmware test covers report decoding.
    for slot, ids in enumerate(all_ids):
        put(base + 0x20004 + slot * 8, get(base + 0x20004 + slot * 8) | (1 << 31))
        put(base + 0x30000 + slot * 4, slot)
    put(mb + 45 * 4, len(submits))
    u.emu_start(u.reg_read(UC_MIPS_REG_PC), 0x9fc0fff0, count=100000)
    assert get(mb + 45 * 4) == len(submits) and not returns
    put(ni + 0x6340, 0x8000007c)
    put(ni + 0x6380, 0x80000000)
    u.emu_start(u.reg_read(UC_MIPS_REG_PC), 0x9fc0fff0, count=100000)
    assert returns == [payload + n * 2048 | 6 for n in range(len(lengths))]
    assert get(mb + 30 * 4) == 0 and get(mb + 29 * 4) == len(lengths)
    assert get(base + 0x18020) == 0
    if source == 6: assert not get(base + 0x1e808)
    for n, frame in enumerate(frames):
        assert bytes(u.mem_read(payload + n * 2048 + 64 + ni_header + 14, len(frame) - 14)) == frame[14:]
        assert get(base + 0x20000 + n * 8) == 0
    return {'lengths': lengths, 'cap': cap, 'MPDUs': len(submits), 'from_EPP': from_epp, 'HW_RTS': hw_rts, 'bandwidth': bandwidth, 'rate': rate}


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[2] == '--160':
        rows = [case([1514] * 4, 7935, True, bandwidth=3, rate=rate) for rate in (0x119, 0x19b)]
    elif len(sys.argv) > 2 and sys.argv[2] == '--dynamic':
        rows = [case([1514] * 8, 10896, True, policy=size) for size in (0,3793,7433,10896)]
        assert [row['MPDUs'] for row in rows] == [8,4,2,2]
        rows += [case([64] * 9, 10896, True, policy=3793)]
        assert rows[-1]['MPDUs'] == 1
    elif len(sys.argv) > 2 and sys.argv[2] == '--he-large':
        rows = [case([1514] * 7, cap, True, bandwidth=3, rate=0x19b) for cap in (7935, 10896)]
        assert [row['MPDUs'] for row in rows] == [2, 1]
    else:
        rows = [case([64 + (n + pad) % 4 for n in range(count)], 7935)
                for count in range(2, 9) for pad in range(4)]
        rows += [case([1500] * 8, 7935), case([1500] * 8, 3839)]
        rows += [case([1514] * 8, 7935, True), case([1514] * 32, 10896, True),
                 case([64, 65, 66, 67], 7935, True)]
        rows += [case([1514] * 4, 7935, True, True)]
        rows += [case([1514] * 7, 10896, True, ni_header=8),
                 case([1514] * 7, 10896, True, compact_epp=True)]
    for enabled in [False, True]:
        rows.append(case([1514] * 7 + [818, 54, 66] + [1514]*7 + [66], 10896, True, preload=enabled))
    print(json.dumps({'cases': rows, 'exact_headers_payload_padding_DMA': True,
                      'one_WP_per_aggregate': True,
                      'ownership_until_RPQ_and_EQ_credit': True}, indent=2))
