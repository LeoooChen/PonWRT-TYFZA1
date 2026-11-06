#!/usr/bin/env python3
"""Execute original multicast wireless helper registration, replicas and CPU TX."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_module_oracle import ModuleOracle
from trace_stock_fttr_mcast_hw import replica_recipes


def run(fc_path, ni_path, mgr_path):
    class HelpersRegistered(Exception):
        pass

    events, registered = [], {}
    driver_result = 0

    def manager_boundary(name, args):
        if name == 'rtk_fc_helper_register':
            if args[0] == 5:
                table = args[1]
                for offset, function in ((160, 'rtk_fc_wlanDevIdx2port'),
                                         (200, 'rtk_fc_wlan_hard_start_xmit')):
                    assert mgr.unpack(table + offset, 'Q')[0] == mgr.symbols[function][0]
                    registered[offset] = function
                raise HelpersRegistered
            return 0
        if name == 'wifi_driver_tx':
            assert args[:2] == [skb, dev]
            assert tx.unpack(skb + 16, 'Q')[0] == dev
            events.append(dict(operation='driver_tx', result=driver_result))
            return driver_result
        if name == 'rtk_fc_fastfwd_directXmit':
            fc.uc.mem_write(skb, bytes(mgr.uc.mem_read(skb, 512)))
            fc.uc.mem_write(frame, bytes(mgr.uc.mem_read(frame, 96)))
            fc.uc.mem_write(desc, bytes(mgr.uc.mem_read(args[1], 16)))
            events.append(dict(operation='direct', vif=fc.unpack(desc + 4, 'I')[0] >> 18))
            return fc.call('rtk_fc_fastfwd_directXmit', skb, desc)
        if name == '_rtk_fc_rx_final_process':
            events.append(dict(operation='receive', reason=mgr.unpack(args[0], 'I')[0] >> 3 & 255))
            return 0
        if name == '__dev_kfree_skb_any':
            assert args[0] == skb
            events.append(dict(operation='drop'))
            return 0
        if name == 'sprintf':
            tx.uc.mem_write(args[0], b'\0')
            return 0
        if name == '__printk_ratelimit':
            return 0
        raise AssertionError(('unmodeled manager boundary', name, args))

    # The manager and FC execute separately. Only the cross-module call ABI
    # is copied; registration, receive conversion and TX selection are real.
    def fc_boundary(name, args):
        if name == 'wlan_hard_start_xmit':
            assert args[1] == skb
            tx.uc.mem_write(skb, bytes(fc.uc.mem_read(skb, 512)))
            tx.uc.mem_write(desc, bytes(fc.uc.mem_read(desc, 16)))
            return tx.call('rtk_fc_wlan_hard_start_xmit', *args[:3])
        raise AssertionError(('unmodeled FC boundary', name, args))

    mgr = ModuleOracle(mgr_path, ['rtk_fc_helper_init', 'rtk_fc_skb_rx'], manager_boundary,
        # Merely stored in unrelated helper tables before the wireless
        # registration boundary; these functions are never executed here.
        external_symbols={'cpu_number': 0x5006000,
                          'rtk_fc_skb_wifi_rx': 0x5006100, 'init_net': 0x5006200})
    tx = ModuleOracle(mgr_path, ['rtk_fc_wlanDevIdx2port', 'rtk_fc_wlan_hard_start_xmit'], manager_boundary,
                      external_symbols={'cpu_number': 0x5006000})
    fc = ModuleOracle(fc_path, ['rtk_fc_helper_register', 'rtk_fc_fastfwd_directXmit'], fc_boundary)
    try:
        mgr.call('rtk_fc_helper_init')
    except HelpersRegistered:
        pass
    assert len(registered) == 2
    table = fc.scratch + 0x3000
    assert fc.call('rtk_fc_helper_register', 5, table) == 0
    assert fc.unpack(fc.symbols['fc_db'][0] + 72, 'Q')[0] == table
    fc.word(table + 200, fc.callback('wlan_hard_start_xmit'))
    skb, frame, desc, dev, ops, private, ha, hc = [mgr.scratch + off for off in
        (0, 0x1000, 0x1100, 0x2000, 0x2800, 0x4000, 0x4100, 0x4200)]
    db = mgr.symbols['fc_mgr_db'][0]
    tx.word(ops + 32, tx.callback('wifi_driver_tx'))
    mgr.word(skb + 200, frame)
    mgr.uc.mem_write(frame, bytes.fromhex('01005e0102030200000000010800') + bytes(82))
    mgr.word(private + 8, ha)
    mgr.word(private + 16, hc)
    mgr.uc.mem_write(db + 4, struct.pack('<I', 1))  # Synchronous dispatch.
    cases = []
    for vif in (1, 17, 55, 56, 60):
        row = db + vif * 632
        tx.word(row + 2648, dev)
        tx.uc.mem_write(row + 2656, struct.pack('<II', 18, 0))
        tx.word(row + 2664, ops)
        for active, running in ((True, True), (False, True), (True, False)):
            tx.word(row + 2672, 1 << 43 if active else 0)
            tx.word(dev + 64, int(running))
            pointer = mgr.scratch + 0x5000
            result = tx.call('rtk_fc_wlanDevIdx2port', vif, pointer, pointer + 4)
            assert result == (0 if active else 0xffffffff)
            if active:
                assert tx.unpack(pointer, '2I') == (18, 0)
            for driver_result in (0, 1):
                events.clear()
                mgr.uc.mem_write(ha, struct.pack('<II', vif << 18, 27 << 9 | 16 << 3 | 96 << 15))
                mgr.uc.mem_write(hc, struct.pack('<II', 0, 4 << 8))
                assert mgr.call('rtk_fc_skb_rx', 0, 0, skb, private) == 2
                if vif == 56:
                    assert events == [dict(operation='receive', reason=17)]
                else:
                    assert events[0] == dict(operation='direct', vif=vif)
                    expected = dict(operation='driver_tx', result=driver_result) if active and running else dict(operation='drop')
                    assert events[1:] == [expected], events
                cases.append(dict(vif=vif, registered=active, running=running,
                                  driver_result=driver_result, events=list(events)))

    qos_cases = []
    for vif in (17, 60):
        row = db + vif * 632
        tx.word(row + 2672, 1 << 43)
        tx.word(dev + 64, 1)
        for enabled in (False, True):
            tx.uc.mem_write(db + 0xabe4, bytes([16 if enabled else 0]))
            tx.uc.mem_write(db + 0xabe5, bytes(reversed(range(8))))
            for cos in range(8):
                events.clear()
                mgr.uc.mem_write(skb + 40, b'\x5a')
                mgr.uc.mem_write(ha, struct.pack('<II', vif << 18,
                    27 << 9 | 16 << 3 | 96 << 15 | cos))
                mgr.uc.mem_write(hc, struct.pack('<II', 0, 4 << 8))
                assert mgr.call('rtk_fc_skb_rx', 0, 0, skb, private) == 2
                assert events == [dict(operation='direct', vif=vif),
                                  dict(operation='driver_tx', result=driver_result)]
                actual = tx.unpack(skb + 40, 'B')[0]
                assert actual == (7-cos if enabled and vif <= 42 else 0x5a)
                qos_cases.append(dict(vif=vif, enabled=enabled, cos=cos, callback_priority=actual))

    recipes = []
    for vif in (1, 17, 55, 60):
        recipes += replica_recipes(fc_path, ni_path, (18,), True, wifi=(vif, 18, 0))['cases']
    return dict(registered_helpers=registered, dispatch=cases, qos=qos_cases, replicas=recipes,
        findings=[
            'Manager helper5+160 is wlanDevIdx2port; helper5+200 is wlan_hard_start_xmit. Member bit16 selects wireless VIF lookup, not PON.',
            'The actual wireless replica producer uses the registered destination and POL=VIF, final S/C tags, source-filter disable and preserved COS.',
            'For an MCE source27 CPU copy, POL becomes the direct-TX VIF; the current live registration and running device select its saved driver TX callback.',
            'Original optional wireless QoS mapping consumes each copy COS for VIF1..42. Native mac80211 instead needs the actual software ingress priority and its current WME/DSCP policy; a single sample cannot stand in for a per-packet value.',
            'POL56 takes the reserved local receive path. This synthetic CPU18/VIF56 route is not a claim that original registration normally allocates such a mapping.',
            'Native radio copies need distinct quarantined ownership from host copies and ordinary misses; raw reusable VIF metadata alone supplies no retirement guarantee.'],
        scope='Actual original ARM64 helper registration, member/action parsing, NI action conversion, CPU receive conversion and TX selection. Registered route rows, cross-module calls, allocation, scheduling and final wireless driver are modeled; no air transmission or retirement proof.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('fc', 'ni', 'mgr', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = run(args.fc, args.ni, args.mgr)
    result['sha256'] = {name: hashlib.sha256(getattr(args, name).read_bytes()).hexdigest()
                        for name in ('fc', 'ni', 'mgr')}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('PASS: original wireless helper registration, 30 CPU dispatch cases, 32 per-copy COS/QoS cases and 12 complete FC/NI replica recipes')


if __name__ == '__main__':
    main()
