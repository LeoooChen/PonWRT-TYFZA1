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
phl, hal, request, stats, feedback, buffers, common, hci = [0x6000000 + i * 0x10000 for i in range(8)]
wd_ring = 0x7000000
now = 100
events = []
releases = []
release_failure = 0
owner = 0


def callback(name, args):
    if name == 'rtw_hal_get_halcom': return hal
    if name == '_rtw_get_current_time': return now
    if name == '_rtw_systime_to_ms': return args[0]
    if name == 'tx_feedback':
        assert args[0] == feedback
        events.append(oracle.unpack(feedback + 0x14, 'I')[0])
        return 0
    if name == 'payload_release':
        assert args[:2] == [common, request]
        assert oracle.unpack(owner, 'Q')[0] == request, 'WP cleared before payload release'
        releases.append((oracle.unpack(request + 0x4c, 'I')[0] >> 24) & 15)
        return release_failure
    if name == 'rtw_phl_get_stainfo_by_macid': return 0
    if name == '__local_bh_enable_ip':
        assert args[1] == 512
        oracle.uc.mem_write(oracle.scratch + 0x9010, bytes(4))
        return 0
    if name == 'printk': return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(a.module, ['_phl_wp_rpt_statistics', '_phl_wp_rpt_chk_txsts', 'phl_recycle_payload'], callback,
                      external_symbols={'vabits_actual': 0x5001000})
oracle.word(0x5001000, 48)
for address in (phl, hal, request, stats, feedback, buffers, common, hci): oracle.uc.mem_map(address, 0x10000)
oracle.uc.mem_map(wd_ring, 0x80000)
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
oracle.word(phl + 0x1228, common)
oracle.word(phl + 0x5d20, hci)
oracle.word(hci + 0x18, wd_ring)
oracle.word(common + 0x31c8, oracle.callback('payload_release'))
oracle.word(common + 0x7ee0, common)
recycle_rows = []
for channel in (0, 1, 2, 3, 8, 9):
    for status in (0, 1, 2, 3, 4, 5):
        for release_failure in (1, 0):
            for wp in (0, 37, 4094):
                owner = wd_ring + channel * 0x80a8 + 0xa8 + wp * 8
                oracle.word(owner, request)
                oracle.uc.mem_write(stats + channel * 48, struct.pack('<I', 1))
                events.clear(); releases.clear()
                oracle.call('phl_recycle_payload', phl, channel, wp, status, 3)
                assert events == [status] and releases == [status]
                assert oracle.unpack(owner, 'Q')[0] == (request if release_failure else 0)
                assert oracle.unpack(stats + channel * 48, 'I')[0] == bool(release_failure)
                recycle_rows.append({'channel': channel, 'status': status, 'wp': wp,
                                     'release_failed': bool(release_failure), 'retained': bool(release_failure)})
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps({'module_sha256': hashlib.sha256(a.module.read_bytes()).hexdigest(),
    'scope': 'Actual target status/statistics and payload-recycle functions; modeled clock, HAL, absent STA, feedback/payload callback and BH-enable. No physical DMA retirement.',
    'software_drop': 4, 'cases': rows, 'recycle_cases': recycle_rows}, indent=2) + '\n')
print(f'Target radio software-drop: {len(rows)} age/channel/status cases; status4 feeds explicit drop and changes no air-success/failure statistics')
print(f'Target payload release: {len(recycle_rows)} channel/status/WP/failure cases; WP/count remain owned on callback failure and release only after callback success')
