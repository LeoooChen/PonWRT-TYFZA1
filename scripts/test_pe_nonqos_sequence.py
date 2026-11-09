#!/usr/bin/env python3
"""Execute linked MIPS non-QoS EPP, publisher and completion sequence lifetime."""
from pathlib import Path
import struct

root = Path(__file__).resolve().parents[1]
fixture = root / 'scripts/test_pe_mcast.py'
env = {'__file__': str(fixture), '__name__': 'sequence_fixture'}
exec(compile(fixture.read_text().split('vectors=0', 1)[0], str(fixture), 'exec'), env)
Machine, base, mb, ni, bar, epp, payload, requests = (
    env[n] for n in ('Machine', 'base', 'mb', 'ni', 'bar', 'epp', 'payload', 'requests'))
RING, PAGES, REPORTS, FORWARD = 0x05000000, 0x05100000, 0x05200000, 0x05300000
SEQ = base + 0x1fd00


def machine(start, full=True, client=False, role=0):
    m = Machine(full=full, client=client)
    for address, size in ((RING, 0x4000), (PAGES, 0x32000), (REPORTS, 0x10000), (FORWARD, 0x10000)):
        m.u.mem_map(address, size)
    for index, value in ((11, RING), (12, PAGES), (14, base + 0x4000),
                         (24, 1), (116, 0x6380), (117, 6), (68, REPORTS), (72, FORWARD)):
        m.put(mb + index * 4, value)
    domain=(7+(role>>4)) if role>>4 else role
    m.seq=base+(0x1fd00 if not domain else 0x68c00+(domain-1)*20)
    m.token=27|(domain<<27)
    m.put(m.seq,27);m.put(m.seq+16,start)
    m.put(base+0xd02c,role<<24)
    m.put(base + 0xd028, 24 << 16)
    # Ordinary native NI peer traffic (no multicast cookie). The new AP
    # BCMC producer will share this same non-QoS sequence space.
    m.u.mem_write(payload + 64, struct.pack('>II', 1 << 31 if full else 0,
        (len(m.frame) + m.header - 8) << 15 | 19 << 3))
    m.u.mem_write(payload + 64 + m.header, env['peer'])
    m.ingest()
    assert m.get(payload + 28) == m.token
    m.put(bar + 0xc438, 10000)
    return m


def select(m):
    assert m.call('ni_select', mb, base, requests) == 1
    assert m.get(requests + 124) == m.token
    m.put(mb + 193 * 4, 1)


def publish(m):
    m.call('pe_tx_publish', mb, 8, requests)


cases = 0
for start in (0, 1, 4094, 4095):
    for full in (False, True):
        for client in (False, True):
            m = machine(start, full, client); select(m)
            # No TXBD credit must not consume a sequence.
            m.put(bar + 0x1058, 1 << 16); publish(m)
            assert m.get(SEQ + 16) == start and not m.get(mb + 194 * 4)
            # Independent hardware-tag exhaustion also precedes allocation.
            m.put(bar + 0x1058, 0); m.put(base + 0x3400c, 1); publish(m)
            assert m.get(SEQ + 16) == start and not m.get(mb + 194 * 4)
            m.put(base + 0x3400c, 0)
            # ARM in its allocation section: PE yields without spinning.
            m.put(bar + 0x1058, 0); m.put(SEQ + 4, 1); publish(m)
            assert m.get(SEQ + 16) == start and not m.get(SEQ + 8)
            assert not m.get(mb + 194 * 4)
            # Model a completed Linux allocation between PE attempts.
            m.put(SEQ + 16, (start + 1) & 4095); m.put(SEQ + 4, 0)
            publish(m)
            assert m.get(SEQ + 16) == (start + 2) & 4095
            assert m.get(requests + 124) == 0x80000000 | ((start + 1) & 4095)
            assert m.get(mb + 194 * 4) == 1 and m.get(mb + 10 * 4) == 1
            wd = m.get(RING + 4)
            length, address = struct.unpack('<HI', m.u.mem_read(wd + 64, 6))
            frame = bytes(m.u.mem_read(address, length & 2047))
            expected = bytes.fromhex('08410000' if client else '08420000')
            ap = bytes(m.u.mem_read(mb + 64, 6)); sa = bytes(m.frame[6:12])
            expected += env['peer'] + (sa if client else ap) + (env['peer'] if client else sa)
            expected += struct.pack('<H', ((start + 1) & 4095) << 4)
            expected += bytes.fromhex('aaaa030000000800') + bytes(m.frame[14:])
            assert frame == expected
            assert m.get(payload + 28) == 27  # Header edit preserved generation.
            assert not m.get(base + 0x10020)  # No per-TID counter consumption.
            # Actual RPQ loop marks the WP before EQ returns its BID.
            raw = struct.pack('<II', 24 | 0xc000 | 1 << 16, 7 << 24 | 4)
            raw += bytes(12) + struct.pack('<I', 1 | (m.get(PAGES + 56) & 4095) << 16)
            m.u.mem_write(REPORTS, raw); m.put(bar + 0x121c, 1 << 16)
            from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP
            m.u.mem_map(0x10020000, 0x1000)
            m.put(mb + 3 * 4, 1); m.put(mb + 140 * 4, mb | 0xa0000000); m.put(mb + 142 * 4, 1)
            m.u.reg_write(UC_MIPS_REG_A0, mb | 0xa0000000); m.u.reg_write(UC_MIPS_REG_SP, 0xa7e01ff0)
            m.u.emu_start(env['symbols']['pe_main'], 0x9fc0fff0, count=20000)
            assert m.get(base + 0x20004) & 1 << 31 and m.get(base + 0x20000) == payload
            m.put(ni + 0x6340, 1); m.put(ni + 0x6380, 1 << 31)
            m.call('ni_recycle', mb, base)
            assert not m.get(base + 0x20000) and not m.get(mb + 30 * 4)
            assert m.get(SEQ + 16) == (start + 2) & 4095
            cases += 1

# Generation withdrawal while queued, selected, or already assigned but
# delayed on the PN allocator. Never allocate again on a publication retry.
for phase in ('queue', 'selected', 'pn_stall', 'pn_retry'):
    m = machine(4095)
    if phase != 'queue': select(m)
    if phase.startswith('pn_'):
        key = base + 0x1e900
        m.put(key, 9); m.put(key + 4, 1)
        m.put(requests + 16, (m.get(requests + 16) & ~(1 << 16)) | 1 << 15)
        m.put(requests + 120, 9)
        publish(m)
        assert m.get(SEQ + 16) == 0 and not m.get(mb + 194 * 4)
        assert m.get(requests + 124) == 0x80000fff
        if phase == 'pn_retry':
            m.put(key + 4, 0); publish(m)
            assert m.get(SEQ + 16) == 0 and m.get(mb + 10 * 4) == 1
            cases += 1
            continue
    m.put(SEQ, 28)
    if phase == 'queue':
        assert not m.call('ni_select', mb, base, requests)
        assert m.get(mb + 47 * 4) == 2049
    else:
        publish(m)
        assert m.get(mb + 194 * 4) == 1 and m.get(mb + 45 * 4) == 1
    assert not m.get(mb + 10 * 4)
    assert m.get(SEQ + 16) == (0 if phase == 'pn_stall' else 4095)
    cases += 1
# CPU preassigned sequences must survive the complete main-loop request
# copy, which normally copies only the descriptor and used segment words.
fixture = root / 'scripts/test_pe_tx_publication.py'
cpu = {'__file__': str(fixture), '__name__': 'sequence_cpu_fixture'}
exec(compile(fixture.read_text().split('for initial_sequence in (0, 15):', 1)[0], str(fixture), 'exec'), cpu)
for sequence in (0, 1, 4094, 4095):
    m = cpu['Machine'](paired=False); tx, mailbox = cpu['TX'], cpu['TXMB']
    data = cpu['PAYLOAD']; header = tx + 0x4000 + 0x10000
    m.put(mailbox + 8 * 4, 1); m.put(mailbox + 9 * 4, 0); m.put(mailbox + 14 * 4, header)
    m.put(tx + 0x1fd00, 27); m.put(tx + 0x1fd10, 33)
    ethernet = bytes.fromhex('0200000000020200000000010800') + bytes(66)
    m.u.mem_write(data, ethernet); m.u.mem_write(header + 7 * 128, ethernet[:14])
    words = [0] * 32
    words[:6] = [7, 0, 98 | 24 << 16 | 1 << 24, 4, 1 << 24 | 1 << 18, 0]
    words[8:10] = [data, 80]; words[31] = 0x80000000 | sequence
    m.u.mem_write(tx + 0x4000, struct.pack('<32I', *words)); m.run('PE1', 18000)
    assert m.get(mailbox + 194 * 4) == 1 and m.get(tx + 0x1fd10) == 33
    wd = cpu['PAGES']; assert m.get(wd + 12) & 4095 == sequence
    assert struct.unpack('<H', m.u.mem_read(header + 7 * 128 + 22, 2))[0] == sequence << 4
    cases += 1
print(f'Linked PE shared sequence: {cases} EPP/header/RPQ/EQ, CPU request copy, wrap, credit/ARM/PN stalls and generation withdrawal cases passed')

# Domain selection follows the queued role, not the root sequence counter.
for role in list(range(8))+[0x10,0x20,0x30,0x40]:
    for withdraw in (False,True):
        m=machine(4095,role=role);select(m)
        if m.seq!=SEQ:m.put(SEQ,27);m.put(SEQ+16,123)
        if withdraw:m.put(m.seq,28)
        publish(m)
        assert m.get(mb+194*4)==1
        if withdraw:
            assert not m.get(mb+10*4) and m.get(m.seq+16)==4095
        else:
            assert m.get(mb+10*4)==1 and m.get(m.seq+16)==0
            assert m.get(RING+4) and m.get(m.get(RING+4)+12)&4095==4095
        if m.seq!=SEQ:assert m.get(SEQ+16)==123
# CPU preassigned numbers remain revocable after selection and before PCIe.
for domain in range(12):
    for phase in ('queued','selected'):
        m=cpu['Machine'](paired=False);tx,mailbox=cpu['TX'],cpu['TXMB'];data=cpu['PAYLOAD']
        seq=tx+(0x1fd00 if not domain else 0x68c00+(domain-1)*20)
        m.put(seq,7);m.put(seq+16,99);m.put(tx+0x68800+7*4,7|domain<<27)
        header=tx+0x14000;m.put(mailbox+14*4,header)
        m.u.mem_write(data,ethernet);m.u.mem_write(header+7*128,ethernet[:14])
        words=[0]*32;words[:6]=[7,0,98|24<<16|1<<24,4,1<<24|1<<18,0];words[8:10]=[data,80];words[31]=0x80000123
        m.u.mem_write(tx+0x4000,struct.pack('<32I',*words));m.put(mailbox+9*4,0);m.put(mailbox+8*4,1)
        if phase=='selected':
            m.put(cpu['BAR']+0x1058,1<<16);m.run('PE1',12000);assert not m.get(mailbox+10*4)
        m.put(seq,8);m.put(cpu['BAR']+0x1058,0);m.run('PE1',15000)
        assert m.get(tx+0x1e400+7*4)==1 and not m.get(mailbox+10*4) and m.get(seq+16)==99
print('Linked per-VIF sequences: 24 NI domain/withdrawal and24 stale CPU queued/selected requests passed')

# An old domain can remain alive while a station template moves to another.
# Same generation values in both domains must not make this an alias.
for phase in ('queue','selected'):
    m=machine(99,role=1);m.put(base+0x68c00+20,27);m.put(base+0x68c00+20+16,177)
    if phase=='selected':select(m);m.put(requests+12,2<<24)
    else:m.put(base+0xd02c,2<<24)
    if phase=='selected':publish(m);assert m.get(mb+194*4)==1
    else:assert not m.call('ni_select',mb,base,requests)
    assert not m.get(mb+10*4) and m.get(m.seq+16)==99 and m.get(base+0x68c00+20+16)==177
print('NI sequence domain changes reject queued/selected old requests even when both generations match')
