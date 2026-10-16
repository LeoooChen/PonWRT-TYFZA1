#!/usr/bin/env python3
"""Execute stock stop requests, callback acknowledgements and idle queries."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_module_oracle import ModuleOracle

module = ROOT / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
PHL, TASK, OPS = 0x6000000, 0x6010000, 0x6020000
calls = []
idle = 0


def callback(name, args):
    if name == 'printk': return 0
    calls.append({'call': name, 'TX_state': oracle.unpack(PHL + 0x5498, 'I')[0],
                  'RX_state': oracle.unpack(PHL + 0x54a4, 'I')[0]})
    if name == 'phl_check_xmit_ring_resource': return 0
    if name == 'phl_check_recv_ring_resource': return 1
    if name == 'rtw_phl_is_phl_rx_idle': return idle
    if name in ('tx_service', 'rx_service', 'phl_free_deferred_tx_ring',
                'rtw_hal_restore_rx_interrupt'): return 0
    raise AssertionError((name, args))


names = ['phl_req_tx_stop_pcie', 'phl_req_rx_stop_pcie', '_phl_tx_callback_pcie',
         '_phl_rx_callback_pcie', 'phl_datapath_chk_trx_pause',
         'phl_is_tx_sw_pause_pcie', 'phl_is_rx_sw_pause_pcie']
oracle = ModuleOracle(module, names, callback)
oracle.uc.mem_map(PHL, 0x40000)
oracle.word(TASK + 120, PHL)
oracle.word(PHL + 23848, OPS)
oracle.word(OPS + 32, oracle.callback('tx_service'))
oracle.word(OPS + 40, oracle.callback('rx_service'))
rows = []
for direction, offset in (('tx', 0x5498), ('rx', 0x54a4)):
    calls.clear()
    oracle.call('phl_req_' + direction + '_stop_pcie', PHL)
    assert oracle.unpack(PHL + offset, 'I')[0] == 2
    assert not oracle.call('phl_is_' + direction + '_sw_pause_pcie', PHL)
    oracle.call('_phl_' + direction + '_callback_pcie', TASK)
    assert oracle.unpack(PHL + offset, 'I')[0] == 3
    stop_calls = list(calls)
    service = next(v for v in stop_calls if v['call'] == direction + '_service')
    assert service[direction.upper() + '_state'] == 2
    calls.clear()
    oracle.call('_phl_' + direction + '_callback_pcie', TASK)
    stopped_calls = list(calls)
    assert not any(v['call'] == direction + '_service' for v in stopped_calls)
    if direction == 'rx':
        assert stopped_calls == [{'call': 'rtw_hal_restore_rx_interrupt', 'TX_state': 3, 'RX_state': 3}]
        assert not oracle.call('phl_is_rx_sw_pause_pcie', PHL)
        idle = 1
    assert oracle.call('phl_is_' + direction + '_sw_pause_pcie', PHL) == 1
    rows.append({'direction': direction, 'request_state': 2, 'acknowledged_state': 3,
                 'stop_callback': stop_calls, 'already_stopped_callback': stopped_calls})
print(json.dumps({'factory_module_sha256': digest, 'actual_functions': names,
                  'RX_stop_query_requires_processing_idle': True, 'rows': rows,
                  'scope': 'Actual stop-state stores, callback branch/ack ordering and pause queries. '
                           'Resource checks, service bodies, deferred free, RX-interrupt restore and '
                           'RX-idle result are explicit callbacks. No physical interrupt or PE-timer equivalence claim.'}, indent=2))
