#!/usr/bin/env python3
"""Extract the actual original XB TSSI register program for board inputs."""
import hashlib
import json
from pathlib import Path
import struct
import re
import sys
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ
from unicorn.arm64_const import UC_ARM64_REG_X4

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
steps = ['halrf_tssi_disable_8192xb', '_halrf_tssi_set_sys_8192xb',
         '_halrf_tssi_ini_txpwr_ctrl_bb_8192xb', '_halrf_tssi_ini_txpwr_ctrl_bb_he_tb_8192xb',
         '_halrf_tssi_set_dck_8192xb', '_halrf_tssi_set_bbgain_split_8192xb',
         '_halrf_tssi_set_tmeter_tbl_8192xb',
         '_halrf_tssi_slope_cal_org_8192xb', '_halrf_tssi_set_alignment_default_8192xb',
         '_halrf_tssi_set_slope_8192xb', '_halrf_tssi_run_slope_8192xb']
names = steps + ['_is_ifem_8192xb', '_is_efem_8192xb', 'halrf_tssi_enable_8192xb',
                 '_halrf_tssi_set_tssi_track_8192xb', '_halrf_tssi_set_txagc_offset_mv_avg_8192xb']
operations = []
memory_reads = set()

def callback(name, args):
    if name == 'halrf_wreg':
        operations.append({'address': args[1] & 0xffffffff,
                           'mask': args[2] & 0xffffffff, 'value': args[3] & 0xffffffff})
        return 0
    if name == 'halrf_wrf':
        operations.append({'RF_path': args[1], 'address': args[2] & 0xffffffff,
                           'mask': args[3] & 0xffffffff,
                           'value': oracle.uc.reg_read(UC_ARM64_REG_X4) & 0xffffffff})
        return 0
    if name == 'printk':
        return 0
    if name == '_rtw_memcpy':
        oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[0]
    if name == '_rtw_memset':
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
        return args[0]
    if name == 'rtw_hal_flash_get_info':
        assert args[1] in (196, 197) and args[3] == 1, args
        oracle.uc.mem_write(args[2], bytes([calibration[13 + args[1] - 196]]))
        return 0
    raise RuntimeError(('Original call needs source input', name, args))

oracle = ModuleOracle(module, names, callback)
rf, hal, phl = 0x6000000, 0x6200000, 0x6210000
oracle.uc.mem_map(rf, 0x100000)
oracle.uc.mem_map(hal, 0x10000)
oracle.uc.mem_map(phl, 0x10000)
oracle.word(rf, phl)
oracle.word(rf + 8, hal)
oracle.uc.mem_write(rf + 16, struct.pack('<I', 32))
oracle.uc.mem_write(rf + 36, struct.pack('<I', 0x38e17f))
oracle.uc.mem_write(rf + 0x4014, b'\x01')
oracle.uc.mem_write(phl + 0x58fa, b'\x36')
oracle.uc.mem_write(hal + 4, struct.pack('<I', 1))
oracle.uc.mem_write(hal + 892, struct.pack('<I', 0))
oracle.uc.mem_write(hal + 896, b'\x00')
oracle.uc.mem_write(hal + 905, b'\x06')
calibration = (root / 'sources/openwrt/files/lib/firmware/realtek/tyfza1/rtl8192xbe/flash-calibration.bin').read_bytes()
tracking = root / 'evidence/wireless-study-20261009-104746/evidence/stock/radio-parameters/rtl8192xbe/RFE54/RTK66287/TXPWR_TrackTSSI.txt'
for path, path_name in enumerate(('A', 'B')):
    for direction, sign in enumerate(('+', '-')):
        match = re.search(r'\[2G\]\[' + path_name + r'\]\[' + re.escape(sign) + r'\]\[ALL\]\s*=\s*\{([^}]+)\}', tracking.read_text())
        values = bytes(int(value) & 255 for value in match[1].split(','))
        assert len(values) == 30
        oracle.uc.mem_write(rf + 0x331f + (path * 2 + direction) * 30, values)

def read_hook(uc, access, address, size, value, user_data):
    for base, label in [(rf, 'rf'), (hal, 'hal'), (phl, 'phl')]:
        if base <= address < base + (0x100000 if label == 'rf' else 0x10000):
            memory_reads.add((label, address - base, size))
            break

oracle.uc.hook_add(UC_HOOK_MEM_READ, read_hook)
programs = []
for path in (0, 1):
    for name in steps:
        if name.startswith('halrf_tssi_disable') and path:
            continue
        operations = []
        oracle.call(name, rf, 0, path)
        programs.append({'function': name, 'path': path, 'operations': operations})
operations = []
oracle.call('halrf_tssi_enable_8192xb', rf, 0)
programs.append({'function': 'halrf_tssi_enable_8192xb', 'path': 'both', 'operations': operations})
report = {'original_host_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'scope': 'actual original machine-code register extraction; hardware verification is separate',
          'inputs': {'RFE': 54, 'band': 0, 'channel': 6, 'hardware_cv': 1, 'normal_TSSI_type': 1},
          'host_memory_reads': sorted(memory_reads), 'programs': programs,
          'thermal_table_and_final_enable_included': True,
          'thermal_flash_A_B': list(calibration[13:15]),
          'tracking_FEM_file': str(tracking.relative_to(root)),
          'tracking_FEM_sha256': hashlib.sha256(tracking.read_bytes()).hexdigest()}
output = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'manifests/radio-stock-XB-TSSI-program.json'
output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'output': str(output), 'steps': len(programs),
                  'operations': sum(len(p['operations']) for p in programs),
                  'host_memory_reads': sorted(memory_reads)}))
