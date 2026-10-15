#!/usr/bin/env python3
"""Run the linked MIPS RPQ consumer against mixed ARM/PE completions."""
import json
from pathlib import Path
import struct

import sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC

binary = (Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'artifacts/pe-radio-production/pe-tx.bin').read_bytes()
base, mailbox, ring, payload, bar, ni = (0x04000000, 0x04002000, 0x05000000,
                                      0x06000000, 0x12000000, 0x10000000)
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
for at, size in ((0x1fc00000, 0x4000), (base, 0x20000), (ring, 0x4000),
                 (payload, 0x10000), (bar, 0x10000), (ni, 0x10000),
                 (0x10020000, 0x1000), (0x10029000, 0x1000)):
    u.mem_map(at, size)
u.mem_write(0x1fc00000, binary)


def put(at, value): u.mem_write(at, struct.pack('<I', value))
def get(at): return struct.unpack('<I', u.mem_read(at, 4))[0]


for index, value in ((0, 0x50455744), (2, bar | 0xa0000000), (3, 1),
                     (11, ring), (116, 0x6380), (117, 6), (140, mailbox | 0xa0000000)):
    put(mailbox + index * 4, value)
put(ni + 0x6340, 0x8000007c)
put(ni + 0x6380, 0x80000000)
returns = []
u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, context:
           returns.append(value), begin=ni + 0x6384, end=ni + 0x6387)
u.reg_write(UC_MIPS_REG_A0, mailbox | 0xa0000000)
u.reg_write(UC_MIPS_REG_SP, (base + 0x1fff0) | 0xa0000000)
pc = 0xbfc00400


def run():
    global pc
    u.emu_start(pc, 0xbfc03ffc, count=50000)
    pc = u.reg_read(UC_MIPS_REG_PC)


rpq, forward = 0x06300000, 0x06400000
u.mem_map(rpq, 262144)
u.mem_map(forward, 262144)
put(mailbox + 272, rpq)
put(mailbox + 288, forward)
put(mailbox + 284, 8190)
put(mailbox + 280, 255)
put(mailbox + 292, 255)
put(mailbox + 296, 255)
rpq_consumer, tag = 255, 8190
ni_reports, failed = 0, 0


def packet(data, kind=7, wd_extra=0, wrong_tag=False):
    global tag
    tag = 1 if tag == 8191 else tag + 1
    offset = 4 + (32 if wd_extra & (1 << 31) else 16) + ((wd_extra >> 14) & 3) * 2 + ((wd_extra >> 28) & 7) * 16
    if wd_extra & (1 << 23) and kind != 1:
        offset += 4
    raw = bytearray(offset + len(data))
    struct.pack_into('<II', raw, 0, len(raw) | 0xc000 | (tag + wrong_tag) << 16,
                     kind << 24 | wd_extra | len(data))
    raw[offset:] = data
    u.mem_write(rpq + rpq_consumer * 1024, bytes(raw))
    put(bar + 0x121c, ((rpq_consumer + 1) & 255) << 16 | rpq_consumer)
    return raw, offset


for batch in range(64):
    reports, expected, buffers = [], [], []
    for n in range(16):
        member = n * 2
        meta = 1 << 8 | 42 << 17
        if n == 0:
            meta |= 1 << 16
        put(base + 0xc400 + member * 8, payload + member * 128)
        put(base + 0xc404 + member * 8, meta | (member + 1) << 24)
        put(base + 0xc408 + member * 8, payload + (member + 1) * 128)
        put(base + 0xc40c + member * 8, meta | 1 << 15)
        status, polluted = (n + batch) & 7, (n + batch) & 1
        report = 1 | status << 13 | (256 + member) << 16 | polluted << 31
        reports.append(report)
        ni_reports += 1
        failed += bool(status or polluted)
        buffers.extend([payload + member * 128 | 6, payload + (member + 1) * 128 | 6])
        if not n:
            expected.append(report)
        if n % 4 == 0:
            report = 2 | (batch * 16 + n & 255) << 16 | 0x12 << 8
            reports.append(report)
            expected.append(report)
    put(mailbox + 120, 32)
    put(ni + 0x6340, 0)
    raw, offset = packet(struct.pack('<%dI' % len(reports), *reports))
    old_returns = len(returns)
    run()
    assert get(mailbox + 280) == (rpq_consumer + 1) & 255
    assert get(mailbox + 284) == tag
    assert get(mailbox + 300) == ni_reports and get(mailbox + 304) == failed
    assert get(mailbox + 120) == 32 and len(returns) == old_returns
    slot = (batch + 255) & 255
    out = bytes(u.mem_read(forward + slot * 1024, offset + len(expected) * 4))
    assert out[:4] == raw[:4]
    assert struct.unpack_from('<I', out, 4)[0] == 7 << 24 | len(expected) * 4
    assert out[offset:] == struct.pack('<%dI' % len(expected), *expected)
    put(mailbox + 296, (slot + 1) & 255)
    put(ni + 0x6340, 0x8000007c)
    run()
    assert get(mailbox + 120) == 0 and returns[old_returns:] == buffers
    rpq_consumer = (rpq_consumer + 1) & 255

# Full ARM handoff ring holds physical RPQ ownership until the host consumes.
put(mailbox + 296, (get(mailbox + 292) + 1) & 255)
raw, offset = packet(b'control-ACK', kind=10, wd_extra=(1 << 14) | (1 << 23))
run()
assert get(mailbox + 280) == rpq_consumer
put(mailbox + 12, 2)
run()
assert get(mailbox + 16) == 2
put(mailbox + 296, get(mailbox + 292))
run()
assert get(mailbox + 280) == rpq_consumer
put(mailbox + 12, 3)
run()
assert get(mailbox + 280) == (rpq_consumer + 1) & 255
out = bytes(u.mem_read(forward + 63 * 1024, len(raw)))
assert out == raw
assert get(mailbox + 308) == 0

# A full factory DFS batch is516 bytes including RXBD/WD. Preserve it
# through the physical RPQ and the PE handoff, including the channel boundary.
dfs = bytes(8) + b''.join(struct.pack('<Q', 5 << 28 | 9 << 24 | 3 << 20 | i << 56)
                            for i in range(61))
rpq_consumer = get(mailbox + 280)
slot = get(mailbox + 292)
put(mailbox + 296, slot)
raw, offset = packet(dfs, kind=8)
assert len(raw) == 516
run()
assert bytes(u.mem_read(forward + slot * 1024, len(raw))) == raw
put(mailbox + 296, get(mailbox + 292))
rpq_consumer = get(mailbox + 280)
slot = get(mailbox + 292)
end = (rpq_consumer + 1) & 255
put(mailbox + 173 * 4, end | 256)
packet(dfs, kind=8)
run()
assert get(mailbox + 280) == end and get(mailbox + 292) == slot
rpq_consumer = end
raw, offset = packet(dfs, kind=8)
run()
assert get(mailbox + 173 * 4) == 0
assert bytes(u.mem_read(forward + slot * 1024, len(raw))) == raw

# The other physical queue sends radar reports through its ARM slow ring.
# An old report returns its RXBD; the first report on the new channel is kept.
rx_addresses = 0x06600000
u.mem_map(rx_addresses, 4096)
for id, offset in enumerate((0, 1, 3, 2)):
    put(rx_addresses + id * 4, payload + offset * 12288)
put(mailbox + 320, rx_addresses)
rxbd = 0x06500000
u.mem_map(rxbd, 4096)
put(mailbox + 134 * 4, rxbd)
put(mailbox + 177 * 4, 2)
put(base + 0x1a400, 2)
put(base + 0x1a404, 3)
put(base + 0x17400, 0)
put(base + 0x17404, 1)
put(mailbox + 172 * 4, 1 | 256)
for scan in (0, 1):
    raw = bytearray(20 + len(dfs))
    struct.pack_into('<II', raw, 0, len(raw) | 0xc000 | (scan + 1) << 16,
                     8 << 24 | len(dfs))
    raw[20:] = dfs
    u.mem_write(payload + scan * 12288, bytes(raw))
    put(bar + 0x1218, (scan + 1) << 16)
    run()
    assert get(mailbox + 324) == scan + 1
    assert get(mailbox + 376) == scan
assert get(mailbox + 172 * 4) == 0

result = dict(linked_MIPS_C_firmware=True, NI_MPDU=1024, BID_returns=2048,
              ARM_reports_forwarded=256, EOSP_reports_forwarded=64,
              C2H_shift_and_MAC_info_preserved=True, RPQ_and_handoff_wrap=True,
              tag_8191_to_1=True, no_BID_return_without_EQ_credit=True,
              no_ARM_per_NI_completion=True, handoff_full_holds_RPQ=True,
              paused_PE_does_not_consume=True, factory_DFS_61_pulses_preserved=True,
              old_DFS_discarded_on_RXQ_and_RPQ=True, new_DFS_preserved=True, hardware_test=False)
(root / 'logs/pe-rpq-firmware-test.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
