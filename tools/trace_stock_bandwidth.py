#!/usr/bin/env python3
"""Extract actual C/XB 20/40/80 MHz BB/ADC operations before extending the port."""
from pathlib import Path
import hashlib
import json
import struct
from arm64_module_oracle import ModuleOracle
root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['halbb_ctrl_bw_8852c', 'halbb_adc_cfg_8852c', 'halbb_edcca_per20_bitmap_sifs_8852c', 'halbb_ctrl_bw_8192xb',
         'halbb_bw_setting_8192xb', 'halbb_edcca_per20_bitmap_sifs_8192xb',
         'rtw_hal_mac_set_bw', 'cfg_mac_bw', 'rtw_hal_bb_get_txsc', 'halbb_get_txsc']
writes = []
registers = {}
def callback(name, args):
    if name == 'printk': return 0
    if name == 'set_hw_value':
        assert args[1] == 125
        oracle.uc.mem_write(param, bytes(oracle.uc.mem_read(args[2], 8)))
        return 0
    if name.startswith('read'):
        return registers.get(args[1], 0)
    if name.startswith('write'):
        width = int(name[5:])
        registers[args[1]] = args[2] & ((1 << width) - 1)
        writes.append({'operation':name,'address':hex(args[1]),'value':hex(registers[args[1]])})
        return 0
    if name in ('halbb_set_reg', 'halbb_set_reg_cmn'):
        address, mask, value = args[1:4]
        shift = (mask & -mask).bit_length() - 1
        registers[address] = (registers.get(address, 0) & ~mask) | (value << shift & mask)
        writes.append({'address': hex(address), 'mask': hex(mask), 'value': hex(value)})
        return 0
    if name in ('halbb_get_reg', 'halbb_get_reg_cmn'):
        address, mask = args[1:3]
        return (registers.get(address, 0) & mask) >> ((mask & -mask).bit_length() - 1)
    raise RuntimeError((name, args))
oracle = ModuleOracle(module, names, callback)
bb, phl, hal = 0x6000000, 0x6010000, 0x6020000
adapter, ops, io, common, param = [0x6030000 + i * 0x10000 for i in range(5)]
for address in (bb, phl, hal, adapter, ops, io, common, param): oracle.uc.mem_map(address, 0x10000)
oracle.word(bb, phl); oracle.word(bb + 8, hal)
oracle.word(adapter, ops); oracle.word(ops, io); oracle.word(adapter + 8, common)
oracle.word(common + 1888, hal); oracle.word(hal + 328, adapter); oracle.word(hal + 336, bb)
oracle.word(ops + 1992, oracle.callback('set_hw_value'))
for offset, name in [(0,'read8'),(8,'write8'),(32,'read32'),(40,'write32')]:
    oracle.word(io + offset, oracle.callback(name))
oracle.uc.mem_write(phl + 0x58fa, b'\x36')  # actual RFE54
records = []
for chip in ('8852c', '8192xb'):
    for width, enum, indices in [(20, 0, [0]), (40, 1, [1, 2]), (80, 2, [1, 2, 3, 4])]:
        if chip == '8192xb' and width == 80:
            # 2.4GHz board radio operates with 20/40MHz channels.
            continue
        for primary_index in indices:
            writes.clear(); registers.clear()
            status = oracle.call('halbb_ctrl_bw_' + chip, bb, primary_index, enum, 0)
            assert status == 1, (chip, width, primary_index, status)
            records.append({'chip':chip, 'width_MHz':width, 'bandwidth_enum':enum,
                            'primary_index':primary_index, 'PHY':0, 'status':status,
                            'writes':list(writes)})
            center = (36 if width == 20 else 38 if width == 40 else 42) if chip == '8852c' else 6
            primary = center if width == 20 else center + (2 if primary_index == 1 else -2) if width == 40 else center + {1:2,2:-2,3:6,4:-6}[primary_index]
            writes.clear(); registers.clear()
            assert oracle.call('rtw_hal_mac_set_bw',hal,0,primary,center,0,int(chip=='8852c'),enum) == 0
            assert oracle.call('cfg_mac_bw',adapter,param) == 0
            records[-1]['MAC_parameter'] = bytes(oracle.uc.mem_read(param,8)).hex()
            records[-1]['primary_channel'] = primary
            records[-1]['center_channel'] = center
            records[-1]['MAC_writes'] = list(writes)
(root / 'manifests/radio-stock-bandwidth.json').write_text(json.dumps({
    'scope': __doc__, 'original_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
    'executed_functions':names, 'inputs':'RFE54, PHY0/MACband0, DBCC0, BB and MAC writers captured separately; RF/RFK/TPU not configured',
    'records':records}, indent=2) + '\n')
for r in records: print(r['chip'], r['width_MHz'], r['primary_index'],len(r['writes']))
