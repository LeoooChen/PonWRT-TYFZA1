#!/usr/bin/env python3
"""Exercise physical RXQ ownership and level-to-event handoff in linked MIPS code."""
import hashlib
import json
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
if len(sys.argv) == 1:
    sys.argv.append(str(root / 'artifacts/live-radio-pe-rxq-5g/pe-tx.bin'))
fixture = (root / 'scripts/test_pe_ni_radio_firmware.py').read_text().split('for batch in range(64):')[0]
exec(compile(fixture, str(root / 'scripts/test_pe_ni_radio_firmware.py'), 'exec'))
rxq = 0x06800000
u.mem_map(rxq, 256 * 12288)
put(mailbox + 320, rxq)
put(mailbox + 256, 0xb00290b0)
put(mailbox + 324, 254)
put(mailbox + 328, 8190)
put(mailbox + 332, 254)
put(mailbox + 336, 254)
put(bar + 0x1218, 254 << 16 | 254)
slot, tag = 254, 8190
frames = 0
for batch in range(64):
    begin = slot
    snapshots = []
    previous_tag = tag
    for n in range(8):
        tag = 1 if tag == 8191 else tag + 1
        length = (26, 1518, 11966, 64)[n % 4]
        raw = bytearray(36 + length)
        struct.pack_into('<II', raw, 0, len(raw) | 0xc000 | tag << 16,
                         length | 1 << 31)
        raw[36:] = bytes([(batch * 8 + n) & 255]) * length
        u.mem_write(rxq + slot * 12288, bytes(raw))
        snapshots.append((slot, bytes(raw)))
        slot = (slot + 1) & 255
    put(bar + 0x1218, slot << 16 | begin)
    # Hardware producer ahead of DMA tag: PE yields without publishing or returning.
    put(rxq + begin * 12288, 0)
    run()
    assert get(mailbox + 324) == begin and get(mailbox + 328) == previous_tag
    assert get(bar + 0x1218) & 1023 == begin
    u.mem_write(rxq + begin * 12288, snapshots[0][1])
    old_irq = len(timer_loads)
    run()
    frames += 8
    assert get(mailbox + 324) == slot and get(mailbox + 328) == tag
    assert get(mailbox + 344) == frames
    assert get(mailbox + 332) == begin
    assert len(timer_loads) == old_irq + 1
    for at, raw in snapshots:
        assert bytes(u.mem_read(rxq + at * 12288, len(raw))) == raw
    run()
    assert len(timer_loads) == old_irq + 1
    put(mailbox + 336, slot)
    run()
    assert get(mailbox + 332) == slot and get(bar + 0x1218) & 1023 == slot
    for at, raw in snapshots:
        assert get(rxq + at * 12288) == 0
        assert bytes(u.mem_read(rxq + at * 12288 + 4, len(raw) - 4)) == raw[4:]
    put(mailbox + 264, get(mailbox + 276))
    run()

# An event that arrives before IRQ ACK stays pending and is signalled after ACK.
tag = 1 if tag == 8191 else tag + 1
raw = struct.pack('<5I', 20 | 0xc000 | tag << 16, 7 << 24, 0, 0, 0)
u.mem_write(rxq + slot * 12288, raw)
put(bar + 0x1218, ((slot + 1) & 255) << 16 | slot)
run()
signalled = get(mailbox + 276)
put(mailbox + 312, 1)
run()
assert get(mailbox + 268) > signalled and get(mailbox + 276) == signalled
irq_count = len(timer_loads)
put(mailbox + 264, signalled)
run()
assert len(timer_loads) == irq_count + 1
assert get(mailbox + 276) == get(mailbox + 268)

result = {'binary_sha256': hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest(),
          'linked_MIPS': True, 'RXQ_packets': frames,
          'payload_lengths': [26, 1518, 11966, 64],
          'physical_RXBD_not_returned_until_ARM_release': True,
          'payload_and_metadata_preserved': True, 'wrong_tag_yields': True,
          'consumer_wrap': True, 'tag_8191_to_1': True,
          'event_while_IRQ_outstanding_not_lost': True, 'hardware_test': False}
(root / 'logs/pe-rxq-firmware-test.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
