#!/usr/bin/env python3
"""Execute the board's NI completion function with controlled read cursors."""
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_module_oracle import ModuleOracle

read_cursor, released = 0, []


def callback(name, args):
    if name == 'aal_ni_get_tx_read_ptr':
        return read_cursor
    if name == '__ca_free_skb_any':
        released.append(args[0] - 0x06000000)
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(ROOT / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',
                      ['ca_dma_tx_complete_easy'], callback)
queue = oracle.symbols['ni_info_data'][0]
records = []
for finished, hardware in [(0, 2), (0, 3), (0, 4), (60, 2)]:
    released = []
    read_cursor = hardware
    oracle.uc.mem_write(queue + 0x40, struct.pack('<H', finished))
    oracle.uc.mem_write(queue + 0x70, struct.pack('<I', 64))
    for index in range(64):
        oracle.word(queue + 136 + index * 8, 0x06000000 + index)
    count = oracle.call('ca_dma_tx_complete_easy', 0, 0)
    assert count == len(released)
    records.append(dict(finished=finished, hardware_read=hardware, released_indices=released,
                        next_finished=oracle.unpack(queue + 0x40, 'H')[0]))
assert [r['released_indices'] for r in records[:3]] == [[], [0], [0, 1]]
assert records[3]['released_indices'] == [60, 61, 62, 63]
print(json.dumps(dict(scope='Complete original AArch64 ca_dma_tx_complete_easy with a controlled read-pointer callback and skb-release observer; no hardware DMA.',
                      nonwrapped_two_consumed_tail_descriptors_retained=True, records=records), indent=2))
