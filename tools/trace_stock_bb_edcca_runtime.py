#!/usr/bin/env python3
"""Execute original C/XB EDCCA policy and record its complete register writes."""
from pathlib import Path
import hashlib
import json
import struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
bb, hal, phl = 0x6000000, 0x6010000, 0x6020000
registers, writes = {}, []


def callback(name, args):
    if name in ('halbb_set_reg', 'halbb_get_reg'):
        _, address, mask, value = args
        shift = (mask & -mask).bit_length() - 1
        if name == 'halbb_get_reg':
            return (registers.get(address, 0) & mask) >> shift
        before = registers.get(address, 0)
        after = (before & ~mask) | ((value << shift) & mask)
        registers[address] = after
        writes.append(dict(address=hex(address), mask=hex(mask), value=value,
                           before=hex(before), after=hex(after)))
        return 0
    if name in ('halbb_edcca_get_result', 'halbb_edcca_log'):
        return 0
    raise AssertionError((name, args))


names = ['halbb_edcca', 'halbb_edcca_abort', 'halbb_edcca_init',
         'halbb_edcca_thre_calc', 'halbb_edcca_thre_transfer_rssi',
         'halbb_set_edcca_thre', 'halbb_set_collision_th',
         'halbb_set_collision_thre', 'halbb_cr_cfg_edcca_init']
oracle = ModuleOracle(module, names, callback)
oracle.uc.mem_map(bb, 0x30000)
cases = [
    dict(name='C_normal_unlinked', chip='8852c', band=1, mode=0, linked=0, rssi=0),
    dict(name='C_normal_linked_weak', chip='8852c', band=1, mode=0, linked=1, rssi=80),
    dict(name='C_normal_linked_strong', chip='8852c', band=1, mode=0, linked=1, rssi=180),
    dict(name='C_adaptivity_5G', chip='8852c', band=1, mode=1, linked=1, rssi=120),
    dict(name='XB_adaptivity_2G', chip='8192xb', band=0, mode=1, linked=1, rssi=120),
    dict(name='XB_carrier_sense', chip='8192xb', band=0, mode=2, linked=1, rssi=120),
    dict(name='C_mode3_6G_40', chip='8852c', band=2, mode=3, linked=1, rssi=120, bandwidth=1),
    dict(name='C_mode3_6G_160', chip='8852c', band=2, mode=3, linked=1, rssi=120, bandwidth=3),
    dict(name='C_mode3_domain2', chip='8852c', band=2, mode=3, linked=1, rssi=120, domain=2),
    dict(name='C_paused', chip='8852c', band=1, mode=1, linked=1, rssi=120, paused=1),
    dict(name='XB_ability_disabled', chip='8192xb', band=0, mode=0, linked=1, rssi=120, disabled=1),
]
rows = []
for case in cases:
    oracle.uc.mem_write(bb, bytes(0x30000))
    oracle.word(bb, phl)
    oracle.word(bb + 8, hal)
    oracle.uc.mem_write(bb + 0xc14, struct.pack('<III', 8 if case['chip'] == '8852c' else 32, 0, 2))
    oracle.word(bb + 0xd00, 0 if case.get('disabled') else 1 << 4)
    oracle.word(bb + 0xd10, (1 << 4) if case.get('paused') else 0)
    oracle.uc.mem_write(hal + 0x37c, struct.pack('<II', case['band'], case.get('bandwidth', 0)))
    oracle.uc.mem_write(phl + 0x320c, struct.pack('<I', 3))
    oracle.uc.mem_write(phl + 0x64ec, struct.pack('<I', case.get('domain', 0)))
    oracle.uc.mem_write(phl + 0x78e5, bytes([case['mode']]))
    caps = [60, 65, 53, 65] if case['chip'] == '8852c' else [63, 68, 0, 75]
    oracle.uc.mem_write(phl + 0x5945, bytes(caps))
    oracle.uc.mem_write(bb + 0x424c, bytes([case['linked']]))
    oracle.uc.mem_write(bb + 0x4290, bytes([case['rssi']]))
    registers.clear()
    registers.update({0x4840: 0xabcdf012, 0x4848: 18 << 17, 0xc70: 0x069bfe60,
                      0xc74: 0x13579bdf, 0xc78: 0x2468ace0, 0xc7c: 0x55555555})
    writes.clear()
    oracle.call('halbb_cr_cfg_edcca_init', bb)
    oracle.call('halbb_edcca_init', bb)
    writes.clear()
    oracle.call('halbb_edcca', bb)
    state = bytes(oracle.uc.mem_read(bb + 0x24cc, 0xc))
    if case.get('paused') or case.get('disabled'):
        assert not writes
    else:
        assert len(writes) == 18 + (case['mode'] == 3 and case['band'] == 2 and case['chip'] == '8852c')
    rows.append(dict(inputs=case, low=state[0], high=state[1], difference=state[2],
                     collision=state[9], power_offset=oracle.unpack(bb + 0x24eb, 'B')[0],
                     writes=list(writes)))
print(json.dumps(dict(factory_module_sha256=digest, functions=names, cases=rows,
    scope='Original ARM64 policy, CR mapping and all threshold/collision setters execute. '
          'Register IO is modeled; report collection/logging are callbacks. '
          'The synthetic 6GHz cases identify generic policy branches, not TYFZA1 6GHz support. '
          'No real RF, regulatory validation or throughput claim.'), indent=2))
