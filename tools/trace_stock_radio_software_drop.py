#!/usr/bin/env python3
"""Execute target radio status feedback/statistics for software-drop ownership."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--module', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
phl, hal, request, stats, feedback, buffers = [0x6000000 + i * 0x10000 for i in range(6)]
now = 100
events = []


def callback(name, args):
    if name == 'rtw_hal_get_halcom': return hal
    if name == '_rtw_get_current_time': return now
    if name == '_rtw_systime_to_ms': return args[0]
    if name == 'tx_feedback':
        assert args[0] == feedback
        events.append(oracle.unpack(feedback + 0x14, 'I')[0])
        return 0
    if name == 'printk': return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(a.module, ['_phl_wp_rpt_statistics', '_phl_wp_rpt_chk_txsts'], callback)
for address in (phl, hal, request, stats, feedback, buffers): oracle.uc.mem_map(address, 0x10000)
oracle.word(phl + 0x5de0, hal)
oracle.word(hal + 0x738, stats)
oracle.word(request + 0x70, buffers)
oracle.word(request + 0x80, feedback)
oracle.word(feedback + 0x18, oracle.callback('tx_feedback'))
oracle.uc.mem_write(oracle.symbols['phl_log_components'][0], bytes(4))
rows = []
for now in (100, 2000):
    for channel in (0, 1, 2, 3, 8, 9):
        for status in range(6):
            oracle.uc.mem_write(stats, bytes(12 * 48))
            oracle.uc.mem_write(request + 0x60, bytes(4))
            events.clear()
            oracle.call('_phl_wp_rpt_statistics', phl, channel, 37, status, request)
            oracle.call('_phl_wp_rpt_chk_txsts', phl, channel, 37, status, request)
            words = list(oracle.unpack(stats + channel * 48, '12I'))
            expected = [0] * 12
            if status == 0: expected[8 if now > 1000 else 1] = 1
            elif status in (1, 2, 3):
                expected[8 + status if now > 1000 else 2 + status] = 1
                expected[2] = 1
            elif status != 4: expected[2] = 1
            assert words == expected and events == [status], (now, channel, status, words, events)
            rows.append({'age_ms': now, 'channel': channel, 'status': status, 'statistics': words, 'feedback': events[:]})
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps({'module_sha256': hashlib.sha256(a.module.read_bytes()).hexdigest(),
    'scope': 'Actual target status/statistics functions; modeled clock, HAL pointer and feedback callback. No hardware retirement.',
    'software_drop': 4, 'cases': rows}, indent=2) + '\n')
print(f'Target radio software-drop: {len(rows)} age/channel/status cases; status4 feeds explicit drop and changes no air-success/failure statistics')
