#!/usr/bin/env python3
"""Execute the board's TX reset walk over outstanding WP requests."""
from pathlib import Path
import hashlib
import json
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_module_oracle import ModuleOracle

module = ROOT / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
PHL, HCI, COMMON, WD, BD, H2C, HAL = [0x6000000 + n * 0x10000 for n in range(7)]
calls = []
released = []


def callback(name, args):
    calls.append(name)
    if name == 'phl_recycle_payload':
        assert args[1] == 0 and args[3] == 4
        released.append(args[2])
        oracle.word(WD + 168 + args[2] * 8, 0)
        return 0
    if name == 'rtw_hal_get_halcom': return HAL
    if name == '_rtw_memset':
        assert released == [17, 1023]
        oracle.uc.mem_write(args[0], bytes([args[1]]) * args[2])
        return 0
    if name in ('rtw_release_busy_wd_page', 'rtw_release_pending_wd_page',
                'phl_release_busy_h2c_pkt', 'phl_dump_h2c_pool_stats', 'phl_reset_tx_stats'):
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['phl_trx_reset_pcie'], callback)
oracle.uc.mem_map(PHL, 0x80000)
oracle.word(PHL + 4648, COMMON)
oracle.word(PHL + 23840, HCI)
oracle.word(PHL + 23832, H2C)
oracle.uc.mem_write(HCI, b'\x01')
oracle.word(HCI + 8, BD)
oracle.word(HCI + 24, WD)
oracle.word(BD, BD + 128)
oracle.uc.mem_write(BD + 16, struct.pack('<I', 256))
oracle.uc.mem_write(HAL + 748, struct.pack('<I', 256))
oracle.word(WD + 168 + 17 * 8, 0x6070000)
oracle.word(WD + 168 + 1023 * 8, 0x6070100)
oracle.call('phl_trx_reset_pcie', PHL, 1)
assert released == [17, 1023]
assert calls.index('phl_recycle_payload') < calls.index('_rtw_memset')
print(json.dumps({'factory_module_sha256': digest,
                  'actual_function': 'phl_trx_reset_pcie@0x196698',
                  'occupied_WPs_released_before_TXBD_reset': released,
                  'calls': calls,
                  'scope': 'Actual TX reset WP-table walk and ordering. Payload release, HAL, '
                           'WD/H2C release and memset are explicit callbacks; not a DMA quiescence test.'}, indent=2))
