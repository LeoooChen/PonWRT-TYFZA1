#!/usr/bin/env python3
"""Execute linked PE EPP/PS/WP/RPQ/EQ lifetime for original-source6 packets."""
from pathlib import Path
import struct

root = Path(__file__).resolve().parents[1]
fixture = root / 'scripts/test_pe_mcast.py'
env = {'__file__': str(fixture), '__name__': 'fttr_ni_fixture'}
exec(compile(fixture.read_text().split('vectors=0', 1)[0], str(fixture), 'exec'), env)
Machine, base, mb, ni, bar, epp, payload, table, requests = (
    env[n] for n in ('Machine', 'base', 'mb', 'ni', 'bar', 'epp', 'payload', 'table', 'requests'))
DRAIN = base + 0x1e800
RING, PAGES, REPORTS, FORWARD = 0x05000000, 0x05100000, 0x05200000, 0x05300000


def machine(source=6, cos=0, full=True, cookie=True):
    m = Machine(tid=cos, full=full)
    for address, size in ((RING, 0x4000), (PAGES, 0x32000), (REPORTS, 0x10000), (FORWARD, 0x10000)):
        m.u.mem_map(address, size)
    for index, value in ((11, RING), (12, PAGES), (14, base + 0x4000), (24, 1), (116, 0x6380), (117, 6), (68, REPORTS), (72, FORWARD)):
        m.put(mb + index * 4, value)
    m.put(table + 4, 2)
    m.put(table + 16 + 9 * 96 + 4, 1 | source << 16)
    m.u.mem_write(payload + 64, struct.pack('>II', (1 << 31 if full else 0) | (265 << 18 if cookie else 0),
        (len(m.frame) + m.header - 8) << 15 | source << 9 | 19 << 3 | cos))
    if not cookie: m.u.mem_write(payload + 64 + m.header, env['peer'])
    return m


def drain(m, sequence=1):
    m.put(DRAIN, sequence)
    m.call('ni_fttr_drain', mb, base)


def finish_one(m, count=1):
    m.put(ni + 0x6340, count); m.put(ni + 0x6380, 1 << 31)
    m.call('ni_recycle', mb, base)
    m.call('ni_fttr_drain', mb, base)


cases = 0
for source in (0, 4, 6, 7, 18, 19, 63):
    for cos in range(8):
        for full in (False, True):
            for cookie in (False, True):
                m = machine(source, cos, full, cookie)
                m.ingest()
                assert m.get(DRAIN + 8) == (source == 6)
                assert m.get(payload + 20) == (2 if source == 6 else 0)
                m.put(table + 4, 3); drain(m)
                if source == 6:
                    # Old queued source6 is dropped even for a sleeping peer.
                    m.put(base + 0xe810, 1)
                    assert not m.call('ni_select', mb, base, requests)
                    assert m.get(mb + 47 * 4) and m.get(DRAIN + 8) == 1 and not m.get(DRAIN + 4)
                    m.call('ni_recycle', mb, base)
                    assert m.get(DRAIN + 8) == 1 and not m.get(DRAIN + 4)
                    finish_one(m)
                    assert not m.get(DRAIN + 8) and m.get(DRAIN + 4) == 1
                else:
                    assert m.get(DRAIN + 4) == 1 and m.get(mb + 38 * 4) == 1
                cases += 1

# A request captured after FE withdrawal includes every pre-existing EPP,
# not just those which PE had already fetched. Exercise wrap and stale ACK.
for cos in range(8):
    for initial in (0, 4088):
        m = machine(cos=cos)
        m.put(base + 0x11c00 + cos * 4, initial)
        m.u.mem_write(epp + cos * 4096 + initial, struct.pack('<II', payload | 0x50, 0))
        m.put(ni + 0x7060 + cos * 4, (initial + 8) & 4095)
        m.put(table + 4, 3); m.put(DRAIN + 4, 8); drain(m, 9)
        assert m.get(DRAIN + 16) == 1 << cos and m.get(DRAIN + 4) == 8
        # Invalid EPP cannot be counted as consumed.
        m.put(epp + cos * 4096 + initial, payload)
        m.call('ni_ingest', mb, base); m.call('ni_fttr_drain', mb, base)
        assert m.get(DRAIN + 16) == 1 << cos and not m.get(DRAIN + 8)
        m.put(epp + cos * 4096 + initial, payload | 0x50)
        m.call('ni_ingest', mb, base)
        assert not m.get(DRAIN + 16) and m.get(DRAIN + 8) == 1
        assert m.get(base + 0x15000) == payload  # Closed arrivals never enter a peer queue.
        m.call('ni_select', mb, base, requests); finish_one(m)
        assert m.get(DRAIN + 4) == 9 and not m.get(DRAIN + 8)
        cases += 1

# All eight boundaries must progress despite continuously busy high COS.
m = machine(); m.put(table + 4, 3)
for cos in range(8):
    block = bytes(m.u.mem_read(payload, 2048))
    m.u.mem_write(payload + cos * 512, block[:512])
    m.u.mem_write(epp + cos * 4096, struct.pack('<II', (payload + cos * 512) | 0x50, 0))
    m.put(ni + 0x7060 + cos * 4, 8)
m.put(ni + 0x659c, 255 << 24); drain(m)
for cos in range(8):
    m.call('ni_ingest', mb, base)
    assert not (m.get(DRAIN + 16) & ((1 << (cos + 1)) - 1))
assert not m.get(DRAIN + 16) and m.get(DRAIN + 8) == 8
for i in range(8):
    m.call('ni_select', mb, base, requests); finish_one(m)
assert m.get(DRAIN + 4) == 1 and not m.get(DRAIN + 8); cases += 1

# Selected, unpublished WP is revoked without TXBD credit; published WP
# retains its BID until real RPQ AND the final EQ return.
for published in (False, True):
    m = machine(); m.ingest(); m.put(bar + 0xc438, 4000)
    assert m.call('ni_select', mb, base, requests) == 1
    m.put(mb + 193 * 4, 1)
    if published: m.call('pe_tx_publish', mb, 8, requests)
    m.put(table + 4, 3); drain(m)
    if not published:
        m.put(bar + 0x1058, 1 << 16)  # No credit, but a stale request still releases.
        m.call('pe_tx_publish', mb, 8, requests)
        assert not m.get(mb + 10 * 4)
    else:
        m.call('ni_recycle', mb, base); m.call('ni_fttr_drain', mb, base)
        assert m.get(DRAIN + 8) == 1 and not m.get(DRAIN + 4)
        raw = struct.pack('<II', 24 | 0xc000 | 1 << 16, 7 << 24 | 4) + bytes(12) + struct.pack('<I', 1 | (m.get(PAGES + 56) & 4095) << 16)
        m.u.mem_write(REPORTS, raw); m.put(bar + 0x121c, 1 << 16)
        # RPQ is inlined in this optimized firmware; execute the actual loop.
        from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP
        m.u.mem_map(0x10020000, 0x1000)
        m.put(mb + 3 * 4, 1); m.put(mb + 140 * 4, mb | 0xa0000000); m.put(mb + 142 * 4, 1)
        m.u.reg_write(UC_MIPS_REG_A0, mb | 0xa0000000)
        m.u.reg_write(UC_MIPS_REG_SP, 0xa7e01ff0)
        m.u.emu_start(env['symbols']['pe_main'], 0x9fc0fff0, count=20000)
    assert m.get(DRAIN + 8) == 1 and not m.get(DRAIN + 4)
    m.call('ni_recycle', mb, base)
    assert m.get(DRAIN + 8) == 1
    finish_one(m)
    assert m.get(DRAIN + 4) == 1 and not m.get(DRAIN + 8)
    cases += 1

# A late closed arrival invalidates a formerly empty pending observation.
m = machine(); m.put(ni + 0x7060, 0); m.put(table + 4, 3); drain(m)
assert m.get(DRAIN + 4) == 1
m.put(ni + 0x7060, 8); m.ingest()
assert m.get(DRAIN + 8) == 1 and m.get(base + 0x15000) == payload
drain(m, 2); assert m.get(DRAIN + 4) == 1
m.call('ni_select', mb, base, requests); finish_one(m)
assert m.get(DRAIN + 4) == 2; cases += 1

# All gathered BIDs, including a partial EQ return, retain their own stamp.
for count in (2, 7, 8):
    m = machine(cookie=False)
    original = bytes(m.u.mem_read(payload, 512))
    for i in range(count):
        m.u.mem_write(payload + i * 512, original)
        m.u.mem_write(epp + i * 8, struct.pack('<II', (payload + i * 512) | 0x50, 0))
    m.put(ni + 0x7060, count * 8); m.put(base + 0xe204, 7935 << 18)
    m.call('ni_ingest', mb, base); m.put(bar + 0xc438, 4000)
    assert m.get(DRAIN + 8) == count and m.call('ni_select', mb, base, requests) == count
    m.put(mb + 193 * 4, 1); m.call('pe_tx_publish', mb, 8, requests)
    assert m.get(DRAIN + 8) == count
    # Isolate the actual EQ walker; real RPQ decoding is exercised above.
    m.put(base + 0x20004, m.get(base + 0x20004) | 1 << 31)
    m.put(base + 0x30000, 0); m.put(mb + 45 * 4, 1)
    m.put(table + 4, 3); drain(m)
    finish_one(m, count - 1)
    assert m.get(DRAIN + 8) == 1 and not m.get(DRAIN + 4)
    assert m.get(base + 0x20000) == payload + (count - 1) * 512
    finish_one(m)
    assert not m.get(DRAIN + 8) and m.get(DRAIN + 4) == 1
    cases += 1

# A single cookie/peer is insufficient for aggregation: source6 and LAN
# packets must remain distinct even when neither uses multicast conversion.
m = machine(cookie=False); original = bytes(m.u.mem_read(payload, 512))
m.u.mem_write(payload + 512, original)
m.u.mem_write(payload + 512 + 68, struct.pack('>I', (len(m.frame) + m.header - 8) << 15 | 19 << 3))
m.u.mem_write(epp + 8, struct.pack('<II', (payload + 512) | 0x50, 0))
m.put(ni + 0x7060, 16); m.put(base + 0xe204, 7935 << 18)
m.call('ni_ingest', mb, base); m.put(bar + 0xc438, 4000)
assert m.get(DRAIN + 8) == 1 and m.call('ni_select', mb, base, requests) == 1
assert m.get(mb + 38 * 4) == 1; cases += 1

# Run the actual loop while ARM has paused NI and is changing geometry.
# The new request must wait without touching an invalid EPP register base.
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC
m = machine(); m.u.mem_map(0x10020000, 0x1000)
m.put(mb + 3 * 4, 2); m.put(mb + 140 * 4, mb | 0xa0000000); m.put(mb + 142 * 4, 1)
m.put(mb + 114 * 4, 0x800000); m.put(table + 4, 3); m.put(DRAIN, 1)
m.u.reg_write(UC_MIPS_REG_A0, mb | 0xa0000000)
m.u.reg_write(UC_MIPS_REG_SP, 0xa7e01ff0)
m.u.emu_start(env['symbols']['pe_main'], 0x9fc0fff0, count=10000)
assert not m.get(DRAIN + 12) and not m.get(DRAIN + 4)
m.put(mb + 114 * 4, 0x7060); m.put(mb + 3 * 4, 5)
m.put(ni + 0x6340, 10); m.put(ni + 0x6380, 1 << 31)
m.u.emu_start(m.u.reg_read(UC_MIPS_REG_PC), 0x9fc0fff0, count=30000)
assert m.get(DRAIN + 4) == 1 and not m.get(DRAIN + 8); cases += 1
# An unpublished request has no hardware WP tag. After main separates
# request IDs from tags, revoking even a high request ID must return only
# its software credit and must not release another live request's tag0.
for capacity in (1024, 2048):
    m = machine(); owner = capacity - 1
    m.u.mem_write(base + 0x2e000, struct.pack('<H', owner))
    m.put(base + 0x34004, capacity)
    m.put(base + 0x40000, 8)  # Existing CPU request7 owns channel0/tag0.
    m.ingest(); m.put(bar + 0xc438, 4000)
    assert m.call('ni_select', mb, base, requests) == 1
    assert m.get(requests) == 256 + owner and m.get(base + 0x35000 + owner * 4) == 0xffffffff
    # Completion-ring wrap must also preserve this unpublished sentinel.
    m.put(mb + 45 * 4, 4095); m.put(mb + 46 * 4, 4095)
    m.put(table + 4, 3); m.put(mb + 193 * 4, 1)
    m.call('pe_tx_publish', mb, 8, requests)
    assert not m.get(mb + 45 * 4) and m.get(base + 0x30000 + 4095 * 4) == owner
    before = m.get(base + 0x3400c)
    m.put(ni + 0x6340, 0); m.call('ni_recycle', mb, base)
    assert m.get(DRAIN + 8) == 1 and m.get(base + 0x20000 + owner * 8) == payload
    finish_one(m)
    assert not m.get(DRAIN + 8) and not m.get(base + 0x20000 + owner * 8)
    assert m.get(base + 0x40000) == 8 and m.get(base + 0x3400c) == before
    assert m.get(base + 0x34004) == capacity + 1 and not m.get(mb + 46 * 4)
    cases += 1
print(f'Linked NI/PE FTTR: {cases} source/COS/header/cookie, EPP wrap/invalid descriptor, '
      'all-COS progress, sleeping/selected/published WP, actual RPQ, partial aggregate EQ return, '
      'mixed-source isolation, paused geometry and late-arrival cases passed')
