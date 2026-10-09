#!/usr/bin/env python3
"""Run original alignment arithmetic with codewords read from the actual board."""
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
log = Path(sys.argv[1])
lines = log.read_text().splitlines()
measurements = {}
for path in (0, 1):
    row = next(line for line in lines if line.startswith(f'path={path} tracking='))
    assert 'aligned=1' in row
    cw = [int(value) for value in re.search(r'CW=([\d,]+)', row)[1].split(',')]
    fields = []
    for slot in range(6):
        line = next(line for line in lines if line.startswith(f'path={path} alignment_slot={slot} '))
        fields.append({name: int(value) for name, value in re.findall(r'(before|expected|readback)=(\d+)', line)})
    measurements[path] = {'cw': cw, 'fields': fields}

registers, operations, delays = {}, [], []
point = [0, 0]
def callback(name, args):
    if name == 'halrf_wreg':
        address, mask, value = (args[i] & 0xffffffff for i in (1, 2, 3))
        shift = (mask & -mask).bit_length() - 1
        registers[address] = (registers.get(address, 0) & ~mask) | ((value << shift) & mask)
        operations.append({'address': address, 'mask': mask, 'value': value})
        return 0
    if name == 'halrf_rreg':
        address, mask = (args[i] & 0xffffffff for i in (1, 2))
        if address in (0x1c18, 0x3c18):
            path = int(address == 0x3c18)
            if mask == 0x10000:
                return 1  # Actual completed measurements, not an invented codeword.
            assert mask == 0x3ff
            value = measurements[path]['cw'][point[path]]
            point[path] += 1
            return value
        return (registers.get(address, 0) & mask) >> ((mask & -mask).bit_length() - 1)
    if name == '_rtw_memcpy':
        oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[0]
    if name == 'halrf_delay_us':
        delays.append(args[1])
        return 0
    if name in ('_rtw_get_current_time', '_rtw_systime_to_us', 'printk'):
        return 0
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, ['_halrf_tssi_alimentk_rfe_type_other_8852c',
                              '_halrf_tssi_alimentk_finish_bit_8852c'], callback)
rf, hal = 0x6000000, 0x6200000
oracle.uc.mem_map(rf, 0x100000)
oracle.uc.mem_map(hal, 0x10000)
oracle.word(rf + 8, hal)
oracle.uc.mem_write(hal + 892, struct.pack('<I', 1))
slots = [(0x5638, 0x3ff), (0x5644, 0x3ff), (0x5634, 0x3ff00000),
         (0x5640, 0x3ff00000), (0x5634, 0xffc00), (0x5640, 0xffc00)]
result = []
for path in (0, 1):
    for (address, mask), field in zip(slots, measurements[path]['fields']):
        address += path * 0x2000
        shift = (mask & -mask).bit_length() - 1
        registers[address] = registers.get(address, 0) | (field['before'] << shift)
    operations, delays = [], []
    oracle.call('_halrf_tssi_alimentk_rfe_type_other_8852c', rf, 0, path)
    extracted = [(registers[address + path * 0x2000] & mask) >> ((mask & -mask).bit_length() - 1)
                 for address, mask in slots]
    measured = [field['readback'] for field in measurements[path]['fields']]
    assert extracted == measured, (path, extracted, measured)
    assert point[path] == 4
    powers = [operation['value'] for operation in operations
              if operation['address'] == 0x56cc + path * 0x2000 and operation['mask'] == 0xff80000]
    result.append({'path': path, 'actual_board_CW': measurements[path]['cw'],
                   'original_power_operands': powers, 'original_field_results': extracted,
                   'native_hardware_field_readbacks': measured,
                   'original_delay_us_sequence': delays, 'original_operations': operations})
report = {'original_host_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'board_measurement_log': str(log), 'board_log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
          'scope': 'original ELF arithmetic and register operands with actual board codewords; emulation does not verify RF power',
          'paths': result}
output = Path(sys.argv[2])
output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'output': str(output), 'hardware_fields_equal_original_results': True,
                  'actual_board_codewords': [result[i]['actual_board_CW'] for i in (0, 1)]}))
