#!/usr/bin/env python3
"""Execute original BAR window changes with an empty reorder queue."""
from pathlib import Path
import hashlib
import json
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
phl, sta, reorder = 0x6000000, 0x6010000, 0x6020000
handled = 0
normal_ingress_cache = None


def callback(name, args):
    global handled, normal_ingress_cache
    if name == 'rtw_phl_get_stainfo_by_macid':
        return sta
    if name in ('__local_bh_enable_ip', 'printk'):
        return 0
    if name == 'phl_handle_rx_frame_list':
        links = oracle.unpack(args[1], 'QQ')
        if links == (args[1], args[1]):
            handled += 1
        else:
            assert links == (packet, packet)
            normal_ingress_cache = oracle.unpack(common + 0x7f08, 'Q')[0]
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['rtw_phl_rx_bar', 'phl_rx_reorder', 'phl_rx_handle_normal'], callback, {'vabits_actual': 0x500a000})
oracle.uc.mem_map(phl, 0x70000)
common, packet, frames, sequences = 0x6030000, 0x6040000, 0x6050000, 0x6060000
oracle.word(phl + 0x1228, common)
oracle.word(reorder + 16, sequences)
oracle.word(reorder + 24, sta)
oracle.uc.mem_write(packet + 0x75, bytes([1]))
oracle.uc.mem_write(packet + 0x72, struct.pack('<H', 7))
oracle.word(0x500a000, 48)
oracle.word(sta + 0x928, reorder)
oracle.uc.mem_write(reorder + 38, struct.pack('<H', 64))
rows = []
for old, requested, expected in ((0x123, 0x126, 0x126), (4094, 2, 2), (0x123, 0x123, 0x123), (0x123, 0x122, 0x123)):
    oracle.uc.mem_write(reorder + 32, struct.pack('<H', old))
    oracle.call('rtw_phl_rx_bar', phl, sta, 0, requested)
    head = oracle.unpack(reorder + 32, 'H')[0]
    assert head == expected
    rows.append(dict(old_head=old, BAR_SSN=requested, head_after=head))
# Direct-call scenario: deliberately retain the last-SN cache across BAR.
# The later ingress scenario checks whether upstream handling clears it.
oracle.uc.mem_write(reorder + 32, struct.pack('<H', 0x123))
oracle.uc.mem_write(common + 0x7ef0, struct.pack('<I', 7))
oracle.uc.mem_write(common + 0x7efc, struct.pack('<I', 0x122))
oracle.word(common + 0x7f08, reorder)
oracle.call('rtw_phl_rx_bar', phl, sta, 0, 0x126)
oracle.uc.mem_write(packet + 0x6c, struct.pack('<H', 0x123))
oracle.word(frames, frames)
oracle.word(frames + 8, frames)
assert oracle.call('phl_rx_reorder', phl, packet, frames) == 0
factory_cached_head = oracle.unpack(reorder + 32, 'H')[0]
assert factory_cached_head == 0x127
assert oracle.unpack(frames, 'Q')[0] == packet
# The upstream normal RX entry checks the cache before handing BAR upward.
# Use declared control-frame metadata with sequence0 and QoS0. This is a
# boundary scenario, not proof of every hardware BAR descriptor's contents.
oracle.uc.mem_write(reorder + 32, struct.pack('<H', 0x123))
oracle.uc.mem_write(common + 0x7ef0, struct.pack('<I', 7))
oracle.uc.mem_write(common + 0x7efc, struct.pack('<I', 0x122))
oracle.word(common + 0x7f08, reorder)
oracle.uc.mem_write(packet + 0x6c, struct.pack('<H', 0))
oracle.call('phl_rx_handle_normal', phl, packet)
assert normal_ingress_cache == 0
oracle.call('rtw_phl_rx_bar', phl, sta, 0, 0x126)
assert oracle.unpack(reorder + 32, 'H')[0] == 0x126
print(json.dumps(dict(factory_module_sha256=digest, actual_functions=['rtw_phl_rx_bar@0x137960', 'phl_rx_reorder@0x137c58', 'phl_rx_handle_normal@0x18eb04'],
                     cases=rows, empty_frame_lists_dispatched=handled,
                     retained_cache_function_scenario=dict(BAR_head=0x126, incoming_old_SN=0x123, head_after= factory_cached_head, old_frame_linked_for_indication=True),
                     normal_ingress_scenario=dict(control_metadata_SN=0, cached_last_SN=0x122, cached_reorder_cleared_before_upper_handoff=normal_ingress_cache == 0, BAR_head=0x126),
                     scope='Actual ARM64 BAR and normal/cached reorder branches. Retained-cache direct-call scenario is not a claim of a factory end-to-end bug. Control metadata is declared input; bottom-half, station lookup and list dispatch are callbacks. No firmware or RF.'), indent=2))
