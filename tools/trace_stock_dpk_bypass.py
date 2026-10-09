#!/usr/bin/env python3
"""Extract the original RFE54 DPK bypass branch and its actual register writes."""
import hashlib
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = []
for chip in ('8852c', '8192xb'):
    names += [name + '_' + chip for name in ('halrf_dpk', '_dpk_bypass_check',
              '_dpk_force_bypass', 'halrf_dpk_onoff', 'halrf_kpath')]
operations, repeated_rx_dck = [], []
def callback(name, args):
    if name == 'halrf_wreg':
        operations.append({'address': args[1], 'mask': args[2] & 0xffffffff,
                           'value': args[3] & 0xffffffff})
        return 0
    if name == 'halrf_rx_dck_8852c':
        # Record this separately implemented hardware stage at its boundary.
        repeated_rx_dck.append({'phy': args[1], 'is_AFE': args[2]})
        return 0
    if name == 'printk':
        return 0
    raise RuntimeError(('Unexpected branch', name, args))

oracle = ModuleOracle(module, names, callback)
rf, hal, phl = 0x6000000, 0x6200000, 0x6210000
oracle.uc.mem_map(rf, 0x100000)
oracle.uc.mem_map(hal, 0x10000)
oracle.uc.mem_map(phl, 0x10000)
oracle.word(rf, phl)
oracle.word(rf + 8, hal)
oracle.uc.mem_write(phl + 0x58fa, b'\x36')
oracle.uc.mem_write(hal + 4, struct.pack('<I', 1))
oracle.uc.mem_write(rf + 0x7a94, b'\x01')
report = []
for chip, band in (('8852c', 1), ('8192xb', 0)):
    oracle.uc.mem_write(hal + 892, struct.pack('<I', band))
    operations, repeated_rx_dck = [], []
    bypass = oracle.call('_dpk_bypass_check_' + chip, rf, 0)
    assert bypass == 1
    oracle.call('halrf_dpk_' + chip, rf, 0, 1)
    report.append({'chip': chip, 'RFE': 54, 'band': band, 'hardware_cv': 1,
                   'normal_SCC_DPK_index': 0, 'bypass': bypass,
                   'original_operations': operations, 'post_DPK_RX_DCK_calls': repeated_rx_dck})
output = root / 'manifests/radio-stock-RFE54-DPK-bypass.json'
output.write_text(json.dumps({'original_host_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
                             'scope': 'actual original DPK bypass register operands; post-stage RX_DCK recorded at call boundary',
                             'programs': report}, indent=2) + '\n')
print(json.dumps(report))
