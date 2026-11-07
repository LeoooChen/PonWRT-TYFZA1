#!/usr/bin/env python3
"""Execute linked PE main-loop FTTR revocation, credit stalls and WP reuse."""
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
fixture = root / 'scripts/test_pe_tx_publication.py'
setup = fixture.read_text().split('for initial_sequence in (0, 15):', 1)[0]
env = {'__file__': str(fixture), '__name__': 'fttr_pe_fixture'}
exec(compile(setup, str(fixture), 'exec'), env)
Machine, TX, MB, BAR, RING, PAGES, PAYLOAD, REGISTERS = (
    env[n] for n in ('Machine', 'TX', 'TXMB', 'BAR', 'RING', 'PAGES', 'PAYLOAD', 'REGISTERS'))
TABLE = PAYLOAD + 0x8000
DISCARD = TX + 0x1e400


def generation(m, value):
    m.put(TABLE + 4, value & 0xffffffff)
    m.put(TABLE + 8, value >> 32)


def request(m, slot, ident, ring, stamp, segments=1):
    words = [0] * 32
    # Deliberately obsolete ARM positions: only PE's published index counts.
    words[0:4] = [ident, ring | 76 << 8 | 77 << 16, 80 | segments << 24, 4]
    for i in range(segments):
        words[8 + i * 2:10 + i * 2] = [PAYLOAD + i * 128, 8 if segments == 10 else 80]
    words[28:30] = [stamp & 0xffffffff, stamp >> 32]
    m.u.mem_write(TX + 0x4000 + slot * 128, struct.pack('<32I', *words))


cases = 0
for ni in (0, 1):
    for ring in range(6):
        for old, active in ((2, 3), (2, 4), (0xfffffffe, 0x100000000),
                            (0x100000000, 0x100000002), (0xfffffffffffffffe, 0xffffffffffffffff)):
            m = Machine(paired=False)
            m.put(MB + 24 * 4, ni)
            m.put(MB + 210 * 4, TABLE)
            m.put(MB + 8 * 4, 1); m.put(MB + 9 * 4, 0)
            m.put(MB + (32 + ring) * 4, 254)
            m.put(BAR + REGISTERS[ring], 255 << 16 | 254)  # No TXBD credit.
            generation(m, old)
            request(m, 0, 37, ring, old, 10)
            m.run('PE1', 18000)
            assert m.get(MB + 9 * 4) == 1 and m.get(MB + 193 * 4) == 1
            assert not m.get(MB + 194 * 4) and not m.get(DISCARD + 37 * 4)
            generation(m, active)
            m.put(MB + 3 * 4, 2)  # Pause must drain stale requests without credit.
            events = m.run('PE1', 18000)
            assert m.get(MB + 4 * 4) == 2 and m.get(MB + 194 * 4) == 1
            assert m.get(DISCARD + 37 * 4) == 1
            assert not [e for e in events if e[0] in ('descriptor', 'doorbell')]
            assert m.get(MB + (32 + ring) * 4) == 254 and not m.get(MB + 10 * 4)
            # ARM completes the SW drop then reuses the WP for unrelated data.
            m.put(DISCARD + 37 * 4, 0)
            request(m, 1, 37, ring, 0)
            m.put(MB + 8 * 4, 2); m.put(MB + 3 * 4, 5)
            m.put(BAR + REGISTERS[ring], 1 << 16 | 254)
            events = m.run('PE1', 18000)
            assert m.get(MB + 194 * 4) == 2 and not m.get(DISCARD + 37 * 4)
            assert m.get(MB + (32 + ring) * 4) == 255 and m.get(MB + 10 * 4) == 1
            assert m.get(RING + (ring * 256 + 254) * 8 + 4) == PAGES + ring * 264 * 128
            assert m.get(RING + (ring * 256 + 255) * 8) == 0
            assert [e[4] for e in events if e[0] == 'doorbell'] == [255]
            cases += 1

# Every intermediate low/high publication prefix closes old and new packets.
for stamp, low, high, accepted in ((0, 1, 0, True), (2, 2, 0, True),
                                 (2, 3, 0, False), (4, 5, 0, False),
                                 (4, 4, 0, True), (0x100000000, 1, 0, False),
                                 (0x100000000, 1, 1, False), (0x100000000, 0, 1, True)):
    m = Machine(paired=False)
    m.put(MB + 210 * 4, TABLE); m.put(TABLE + 4, low); m.put(TABLE + 8, high)
    m.put(MB + 8 * 4, 1); m.put(MB + 9 * 4, 0)
    request(m, 0, 0, 0, stamp)
    m.run('PE1', 18000)
    assert bool(m.get(MB + 10 * 4)) == accepted
    assert bool(m.get(DISCARD)) != accepted
    cases += 1

# Close between the PE's low and high reads; the final low read rejects it.
from unicorn import UC_HOOK_MEM_READ
m = Machine(paired=False)
m.put(MB + 210 * 4, TABLE); generation(m, 2)
m.put(MB + 8 * 4, 1); m.put(MB + 9 * 4, 0); request(m, 0, 0, 0, 2)
m.u.hook_add(UC_HOOK_MEM_READ, lambda u, *args: m.put(TABLE + 4, 3), begin=TABLE + 8, end=TABLE + 11)
m.run('PE1', 18000)
assert m.get(DISCARD) and not m.get(MB + 10 * 4)
print(f'Linked PE FTTR: {cases + 1} main-loop stale/current/64-bit carry, all six rings, '
      'NI on/off, ten-segment capture, pause under zero credit, no TXBD hole and WP reuse cases passed')
