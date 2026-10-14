#!/usr/bin/env python3
"""Run the linked MIPS RPQ consumer against mixed ARM/PE completions."""
import json
from pathlib import Path
import struct

root = Path(__file__).resolve().parents[1]
fixture = (root / 'scripts/test_pe_ni_radio_firmware.py').read_text().split('for batch in range(64):')[0]
exec(compile(fixture, str(root / 'scripts/test_pe_ni_radio_firmware.py'), 'exec'))
rpq, forward = 0x06300000, 0x06400000
u.mem_map(rpq, 131072)
u.mem_map(forward, 131072)
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
    u.mem_write(rpq + rpq_consumer * 512, bytes(raw))
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
    out = bytes(u.mem_read(forward + slot * 512, offset + len(expected) * 4))
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
out = bytes(u.mem_read(forward + 63 * 512, len(raw)))
assert out == raw
assert get(mailbox + 308) == 0

result = dict(linked_MIPS_C_firmware=True, NI_MPDU=1024, BID_returns=2048,
              ARM_reports_forwarded=256, EOSP_reports_forwarded=64,
              C2H_shift_and_MAC_info_preserved=True, RPQ_and_handoff_wrap=True,
              tag_8191_to_1=True, no_BID_return_without_EQ_credit=True,
              no_ARM_per_NI_completion=True, handoff_full_holds_RPQ=True,
              paused_PE_does_not_consume=True, hardware_test=False)
(root / 'logs/pe-rpq-firmware-test.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
