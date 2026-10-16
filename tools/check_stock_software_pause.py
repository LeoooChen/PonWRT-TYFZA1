#!/usr/bin/env python3
"""Execute original software TX pause request, completion wait and resume."""
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
phl, ops, request = 0x6000000, 0x6010000, 0x6020000
trace = []
queries = 0


def callback(name, args):
    global queries
    if name == '_phl_get_ctrl_mdl':
        return 4
    if name == 'sw_pause_request':
        trace.append('request_software_pause')
        oracle.uc.mem_write(phl + 0x5498, struct.pack('<I', 2))
        return 0
    if name == 'rtw_phl_tx_req_notify':
        trace.append('notify_TX_processor')
        return 0
    if name == 'sw_pause_done':
        queries += 1
        trace.append('query_pause_done')
        if queries == 1:
            return 0
        oracle.uc.mem_write(phl + 0x5498, struct.pack('<I', 3))
        return 1
    if name == 'rtw_msleep_os':
        assert args[0] == 1
        trace.append('wait_1ms_boundary')
        return 0
    if name == 'sw_resume':
        assert args[1] == 1
        trace.append('resume_software_TX')
        return 0
    if name == 'printk':
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['_phl_sw_tx_pause', '_phl_sw_tx_resume'], callback)
oracle.uc.mem_map(phl, 0x30000)
oracle.word(phl + 0x5d28, ops)
for offset, name in ((0x68, 'sw_pause_request'), (0x78, 'sw_pause_done'), (0x60, 'sw_resume')):
    oracle.word(ops + offset, oracle.callback(name))
oracle.uc.mem_write(phl + 0x5498, struct.pack('<I', 1))
oracle.uc.mem_write(request + 4, struct.pack('<I', 3))
assert oracle.call('_phl_sw_tx_pause', phl, request, 0) == 0
assert queries == 2 and oracle.unpack(phl + 0x54b4, 'I')[0] == 4
pause = list(trace)
assert pause == ['request_software_pause', 'notify_TX_processor', 'query_pause_done', 'wait_1ms_boundary', 'query_pause_done']
trace.clear()
assert oracle.call('_phl_sw_tx_resume', phl, request) == 0
assert oracle.unpack(phl + 0x54b4, 'I')[0] == 0
assert trace == ['resume_software_TX', 'notify_TX_processor']
print(json.dumps(dict(factory_module_sha256=digest,
                     actual_functions=['_phl_sw_tx_pause', '_phl_sw_tx_resume'],
                     pause_calls=pause, resume_calls=trace,
                     scope='Actual ARM64 request/wait/owner-mask/resume instructions. Software TX processor callbacks, owner-ID mapping and wait are explicit boundaries. Does not execute original skb queues, firmware or RF, and does not equate RF scheduler pause with software pause.'), indent=2))
