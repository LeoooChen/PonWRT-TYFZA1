#!/usr/bin/env python3
"""Trace protocol dispatch in TYFZA1's original ARM64 PON initializer."""
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
calls = []
allowed = {
    'ca_spin_lock_init', 'aal_psds_init', 'aal_epon_init', 'aal_gpon_init',
    'aal_xgpon_init', 'aal_pon_mac_max_frame_size_set', '__pon_mac_intr_enable',
    'aal_pon_intr_init', 'aal_pon_opt_rogue_thresh_set', 'ca_printf',
    'aal_epon_deinit', 'aal_gpon_deinit', 'aal_xgpon_deinit',
}


def callback(name, args):
    if name not in allowed:
        raise RuntimeError((name, args))
    if name != 'ca_printf':
        calls.append({'function': name, 'args': args})
    return 0


oracle = ModuleOracle(
    root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',
    ['aal_pon_init', 'aal_pon_deinit'], callback)
records = []
stops = []
for mode, name in enumerate(('epon', 'epon-d10g', 'epon-bi10g', 'gpon', 'xgpon', 'xgspon')):
    calls.clear()
    result = oracle.call('aal_pon_init', 0, mode)
    records.append({'mode': name, 'factory_mode': mode, 'return': result,
                    'calls': list(calls)})
    print(name, result, ' -> '.join(item['function'] for item in calls))
    calls.clear()
    result = oracle.call('aal_pon_deinit', 0, mode)
    expected = 'aal_epon_deinit' if mode < 3 else 'aal_gpon_deinit' if mode == 3 else 'aal_xgpon_deinit'
    assert result == 0 and [item['function'] for item in calls] == [expected]
    stops.append({'mode': name, 'factory_mode': mode, 'return': result,
                  'calls': list(calls)})
state_getters = ModuleOracle(
    root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',
    ['aal_gpon_onu_state_get', 'aal_xgpon_actp_status_get'], callback)
registers = 0x6000000
state_getters.uc.mem_map(registers, 0x10000)
state_getters.word(state_getters.symbols['ca_venus_pon_reg_iobase'][0], registers)
states = []
for family, offset, labels in (
    ('gpon', 0x60bc, ('O1', 'O2', 'O3', 'O4', 'O5', 'O6', 'O7')),
    ('xgpon/xgspon', 0x4d30, ('O1', 'O23', 'O4', 'O5', 'O6', 'O7')),
):
    for state, label in enumerate(labels):
        word = (state << 16) | 0xff if family == 'gpon' else state
        state_getters.uc.mem_write(registers + offset, struct.pack('<I', word))
        state_getters.uc.mem_write(state_getters.scratch, bytes(32))
        if family == 'gpon':
            result = state_getters.call('aal_gpon_onu_state_get', 0, 0, state_getters.scratch)
            value = state_getters.unpack(state_getters.scratch, 'I')[0]
        else:
            result = state_getters.call('aal_xgpon_actp_status_get', 0, state_getters.scratch)
            value = state_getters.unpack(state_getters.scratch, 'B')[0]
        assert result == 0 and value == state, (family, state, result, value)
        states.append({'family': family, 'register_offset': offset,
                       'hardware_state': state, 'standard_state': label})
(root / 'logs/stock-pon-mode-dispatch.json').write_text(json.dumps({
    'scope': __doc__, 'dependency_calls_stubbed_success': True,
    'register_sequences_and_optical_operation_verified': False,
    'records': records, 'deinitialization': stops, 'original_state_getters': states,
}, indent=2) + '\n')
