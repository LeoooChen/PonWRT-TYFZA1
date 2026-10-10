#!/usr/bin/env python3
"""Execute original per-station AMPDU wrapper and CMAC global limit writer."""
from pathlib import Path
import hashlib
import json
import struct
from arm64_module_oracle import ModuleOracle
root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['rtw_hal_cfg_tx_ampdu', 'rtw_hal_mac_set_hw_ampdu_cfg', 'set_hw_ampdu_cfg', 'check_mac_en']
hal, sta, adapter, ops, io, param = [0x6000000 + n * 0x10000 for n in range(6)]
operations = []
registers = {0xc604: 0x80, 0xc610: 0x12340000}
def callback(name, args):
    if name == '_rtw_memset':
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2]); return args[0]
    if name == 'set_hw_value':
        assert args[1] == 114
        oracle.uc.mem_write(param, bytes(oracle.uc.mem_read(args[2], 16))); return 0
    if name.startswith('read'):
        return registers.get(args[1], 0)
    if name.startswith('write'):
        registers[args[1]] = args[2]
        operations.append({'operation': name, 'address': hex(args[1]), 'value': hex(args[2])}); return 0
    raise RuntimeError((name, args))
oracle = ModuleOracle(module, names, callback)
for address in (hal, sta, adapter, ops, io, param): oracle.uc.mem_map(address, 0x10000)
oracle.word(hal + 328, adapter); oracle.word(adapter, ops); oracle.word(ops, io)
oracle.word(ops + 1992, oracle.callback('set_hw_value'))
for offset, name in [(0, 'read8'), (8, 'write8'), (32, 'read32'), (40, 'write32')]:
    oracle.word(io + offset, oracle.callback(name))
oracle.uc.mem_write(adapter + 32, b'\x01\x04'); oracle.uc.mem_write(adapter + 67, b'\x01\x01\x00')
records = []
for count in (64, 128, 256):
    oracle.uc.mem_write(sta + 2506, struct.pack('<H', count))
    assert oracle.call('rtw_hal_cfg_tx_ampdu', hal, sta) == 0
    operations.clear()
    assert oracle.call('set_hw_ampdu_cfg', adapter, param) == 0
    records.append({'maximum_aggregation': count, 'original_parameter': bytes(oracle.uc.mem_read(param, 16)).hex(),
                    'writes': list(operations)})
(root / 'manifests/radio-stock-AMPDU-config.json').write_text(json.dumps({
    'scope': __doc__, 'original_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
    'executed_functions': names, 'initial_registers': {'c604': '80', 'c610': '12340000'},
    'records': records}, indent=2) + '\n')
for row in records: print(row)
