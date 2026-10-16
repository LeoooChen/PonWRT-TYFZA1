#!/usr/bin/env python3
"""Trace original Wi-Fi watchdog dispatch, with explicit callee boundaries."""
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
bb, hal, rf, phl = 0x6000000, 0x6020000, 0x6040000, 0x6060000
base = ['halbb_cmn_info_self_update', 'halbb_ic_hw_setting', 'halbb_env_mntr',
        'halbb_dig', 'halbb_pmac_statistics', 'halbb_basic_dbg_message',
        'halbb_physts_watchdog']
advanced = ['halbb_edcca', 'halbb_dfs', 'halbb_cfo_acc_mode_en', 'halbb_cfo_dm', 'halbb_ra_watchdog',
            'halbb_pwr_ctrl', 'halbb_la_re_trig_watchdog', 'halbb_antenna_diversity',
            'halbb_path_diversity', 'halbb_mccdm_switch']
tracking = ['halrf_tssi_ant_open', 'halrf_tssi_tracking', 'halrf_dpk_tracking',
            'halrf_iqk_tracking', 'halrf_lck_tracking', 'halrf_rx_dck_tracking',
            'halrf_op5k_tracking']
allowed = set(base + advanced + tracking + ['halbb_reset', 'halbb_cfo_acc_mode_en',
              'halbb_cfo_dm', 'halrf_get_thermal', 'rtw_hal_bb_watchdog',
              'rtw_hal_rf_watchdog', 'halbb_edcca_dev_hw_cap',
              'halbb_cmn_info_self_reset', 'halbb_cfo_trk_reset'])
calls, traces = [], []
acc_mode = 0


def callback(name, args):
    assert name in allowed, (name, args)
    calls.append(name)
    if name == 'halbb_cfo_acc_mode_en':
        return acc_mode
    if name == 'halrf_get_thermal':
        return 42 + args[1]
    return 0


oracle = ModuleOracle(module, ['rtw_hal_watchdog', 'halbb_watchdog_normal',
                              'halrf_watchdog', 'halbb_cfo_watchdog',
                              'halbb_cmn_info_self_init', 'halbb_supportability_default',
                              'halbb_dig_abort', 'halbb_cfo_trk_abort'], callback)
oracle.uc.mem_map(bb, 0x80000)
oracle.word(bb, phl)
oracle.word(bb + 8, hal)
oracle.word(bb + 0x518, bb + 0x9000)
oracle.uc.mem_write(phl + 0x58fa, bytes([54]))


def run(function, argument, expected, **inputs):
    calls.clear()
    oracle.call(function, argument)
    assert calls == expected, (function, calls)
    traces.append(dict(function=function, inputs=inputs, calls=list(calls)))


run('rtw_hal_watchdog', hal, ['rtw_hal_bb_watchdog', 'rtw_hal_rf_watchdog'])
capabilities = []
for chip, chip_id, mask in [('8852c', 3, 0x38fa), ('8192xb', 4, 0x8fb)]:
    oracle.uc.mem_write(hal, struct.pack('<I', chip_id))
    run('halbb_cmn_info_self_init', bb,
        ['halbb_edcca_dev_hw_cap', 'halbb_cmn_info_self_reset'], chip=chip)
    assert oracle.uc.mem_read(bb + 0xcfd, 1) == bytes([1])
    for xtal in (0, 1):
        oracle.uc.mem_write(phl + 0x58fd, bytes([xtal]))
        actual = oracle.call('halbb_supportability_default', bb)
        assert actual == (mask if xtal else mask & ~64)
        oracle.word(bb + 0xd00, actual)
        assert oracle.call('halbb_dig_abort', bb) == 0
        calls.clear()
        assert oracle.call('halbb_cfo_trk_abort', bb) == (not xtal)
        assert calls == ([] if xtal else ['halbb_cfo_trk_reset'])
        capabilities.append(dict(chip=chip, RFE=54, antenna_diversity=0,
            independent_xtal=xtal, supportability=hex(actual),
            DIG_aborts=False, CFO_aborts=not xtal, advanced_DM_default=1))
for enabled in (0, 1):
    oracle.uc.mem_write(bb + 0xcfd, bytes([enabled]))
    run('halbb_watchdog_normal', bb, base + (advanced if enabled else []) + ['halbb_reset'],
        advanced_DM_byte=enabled)
run('halrf_watchdog', rf, ['halrf_get_thermal'] * 2 + tracking)
assert bytes(oracle.uc.mem_read(rf + 0x4b, 2)) == bytes([42, 43])
for acc_mode in (0, 1):
    run('halbb_cfo_watchdog', bb,
        ['halbb_cfo_acc_mode_en'] + ([] if acc_mode else ['halbb_cfo_dm']),
        acceleration_mode_callback_result=acc_mode)
print(json.dumps(dict(factory_module_sha256=digest, traces=traces, capabilities=capabilities,
    scope='Actual ARM64 initialization, capability/abort and dispatch functions. '
          'Unselected callees are explicit callbacks: '
          'this proves host dispatch and CFO non-acceleration routing, not that '
          'each algorithm is enabled on this board or that RF registers changed. '
          'No hardware, interrupt, firmware scheduler or throughput is executed.'), indent=2))
