#!/usr/bin/env python3
"""Execute the original TYFZA1 EDCA wrapper, address selector and writer."""
from pathlib import Path
import hashlib
import json
import struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['rtw_hal_mac_set_edca', 'set_hw_edca_param', 'get_edca_addr', 'check_mac_en']
adapter, ops, io, hal, common, param = [0x6000000 + n * 0x10000 for n in range(6)]
records = []
operations = []

def callback(name, args):
    if name == 'set_hw_value':
        assert args[1] == 117
        oracle.uc.mem_write(param, bytes(oracle.uc.mem_read(args[2], 16)))
        return 0
    if name in ('write16', 'write32'):
        operations.append({'operation': name, 'address': hex(args[1]), 'value': hex(args[2])})
        return 0
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, names, callback)
for address in (adapter, ops, io, hal, common, param):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(adapter, ops)
oracle.word(ops, io)
oracle.word(ops + 1992, oracle.callback('set_hw_value'))
oracle.word(io + 24, oracle.callback('write16'))
oracle.word(io + 40, oracle.callback('write32'))
oracle.word(hal + 1888, common)
oracle.word(common + 328, adapter)
oracle.uc.mem_write(adapter + 32, b'\x01\x04')
oracle.uc.mem_write(adapter + 67, b'\x01\x01\x00')
for chip, sifs in [('8852c', 16), ('8192xb', 10)]:
    for name, category, aifsn, ecw_min, ecw_max, txop in [
            ('BE', 0, 3, 4, 10, 0), ('BK', 1, 7, 4, 10, 0),
            ('VI', 2, 2, 3, 4, 94), ('VO', 3, 2, 2, 3, 47)]:
        packed = txop << 16 | ecw_max << 12 | ecw_min << 8 | (sifs + aifsn * 9)
        assert oracle.call('rtw_hal_mac_set_edca', hal, 0, 0, category, packed) == 0
        original_param = bytes(oracle.uc.mem_read(param, 16))
        operations.clear()
        assert oracle.call('set_hw_edca_param', adapter, param) == 0
        records.append({'chip': chip, 'AC': name, 'MAC_band': 0, 'WMM': 0,
                        'AIFSN': aifsn, 'SIFS_us': sifs, 'slot_us': 9,
                        'ECW_min': ecw_min, 'ECW_max': ecw_max, 'TXOP_32us': txop,
                        'wrapper_packed': hex(packed), 'original_parameter': original_param.hex(),
                        'writes': list(operations)})
target = root / 'manifests/radio-stock-EDCA.json'
target.write_text(json.dumps({'scope': __doc__, 'original_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
                              'executed_functions': names, 'records': records}, indent=2) + '\n')
for row in records:
    print(row['chip'], row['AC'], row['writes'])
