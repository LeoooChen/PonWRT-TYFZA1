#!/usr/bin/env python3
"""Execute original RA registration and feedback with explicit host STA inputs."""
from pathlib import Path
import hashlib
import json
import struct
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['rtw_halbb_raregistered', 'halbb_init_ra_by_rssi', 'halbb_nss_mapping',
         'halbb_hw_mode_mapping', 'halbb_get_opt_giltf', 'halbb_ax_giltf_chk',
         'halbb_hw_bw_mapping', 'halbb_hw_bw_mode_chk', 'halbb_chk_bw_under_40',
         'halbb_giltf_trans', 'halbb_stbc_mapping',
         'halbb_ldpc_chk', 'halbb_sgi_chk', 'halbb_ac_n_sgi_chk', 'halbb_ramask_mod',
         'halbb_ramask_trans', 'halbb_set_csi_rate', 'halbb_rssi_lv_dec',
         'halbb_ramask_by_rssi', 'halbb_get_fw_ra_rpt', 'halbb_c2h_ra_parsing',
         'rtw_halbb_c2h_parsing']
commands = []
def callback(name, args):
    if name == 'printk':
        return 0
    if name == 'halbb_fill_h2c_cmd':
        pointer = oracle.uc.reg_read(UC_ARM64_REG_X4)
        commands.append({'class': args[3], 'function': args[2], 'bytes': args[1],
                         'payload': bytes(oracle.uc.mem_read(pointer, args[1])).hex()})
        return 1
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, names, callback)
bb, phl, common, sta, halsta, feedback = [0x6000000 + i * 0x10000 for i in range(6)]
for address in (bb, phl, common, sta, halsta, feedback):
    oracle.uc.mem_map(address, 0x10000)
records = []
for chip, ic, band in [('8852c', 8, 1), ('8192xb', 32, 0)]:
    for mode in ('HT', 'VHT', 'HE'):
        if chip == '8192xb' and mode == 'VHT':
            continue
        for width in (20, 40, 80):
            if chip == '8192xb' and width == 80:
                continue
            for nss in (1, 2):
                for rssi in (20, 40, 70):
                    for address in (bb, phl, common, sta, halsta):
                        oracle.uc.mem_write(address, bytes(0x10000))
                    oracle.word(bb, phl); oracle.word(bb + 8, common)
                    oracle.word(bb + 24, sta)  # actual MACID1 table
                    oracle.uc.mem_write(bb + 1169, b'\x01')
                    oracle.uc.mem_write(bb + 3092, struct.pack('<I', ic))
                    oracle.uc.mem_write(phl + 0x58fa, b'\x36')
                    oracle.uc.mem_write(phl + 0x5875, b'\x02')  # SCC TX NSS2
                    oracle.word(sta + 2688, halsta)
                    oracle.uc.mem_write(sta + 28, b'\x01')
                    oracle.uc.mem_write(sta + 40, struct.pack('<I', band))
                    oracle.uc.mem_write(sta + 44, bytes([{20: 0, 40: 1, 80: 2}[width]]))
                    wmode = (4 if band else 3) | {'HT': 8, 'VHT': 16, 'HE': 32}[mode]
                    oracle.uc.mem_write(sta + 2332, bytes([wmode]))
                    oracle.uc.mem_write(sta + 2656, struct.pack('<I', nss << 20))
                    oracle.uc.mem_write(halsta + 35, bytes([rssi]))
                    oracle.uc.mem_write(halsta + 140, b'\x02')
                    mask = {'HT': 0x0fffffff, 'VHT': 0x3ff3fffff, 'HE': 0xfffffffff}[mode]
                    oracle.word(halsta + 152, mask)
                    commands.clear()
                    status = oracle.call('rtw_halbb_raregistered', bb, sta)
                    assert status == 1 and len(commands) == 1
                    records.append({'chip': chip, 'mode': mode, 'width_MHz': width,
                                    'NSS': nss, 'assoc_RSSI': rssi, 'input_mask': hex(mask),
                                    'status': status, 'H2C': commands[0]})
feedback_vectors = 0
for mode in range(4):
    for rate in range(32):
        for gi in range(6):
            for width in range(3):
                data = struct.pack('<HBBBBH', 1, 37, 0, rate, mode | gi << 2 | width << 5, 0)
                oracle.uc.mem_write(feedback, data)
                oracle.uc.mem_write(halsta + 184, bytes(20))
                oracle.call('rtw_halbb_c2h_parsing', bb, 1, 0, len(data), feedback)
                actual = oracle.unpack(halsta + 184, '3I')
                assert actual == (gi, mode, width), (mode, rate, gi, width, actual)
                assert oracle.unpack(halsta + 196, 'B')[0] == rate
                assert oracle.unpack(halsta + 200, 'B')[0] == 37
                feedback_vectors += 1
report = {'scope': __doc__, 'original_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'executed_functions': names, 'boundary': 'H2C publication/logging mocked; no hardware or firmware execution',
          'inputs': 'MACID1, RFE54, SCC TX2SS, no BF/STBC/LDPC or optional GI capabilities, no noisy flag',
          'feedback': {'vectors': feedback_vectors, 'category': 2, 'class': 1, 'function': 0,
                       'payload_bytes': 8, 'MACID': 'little-endian16 bytes0..1', 'retry_ratio': 'byte2',
                       'rate': 'byte4 bits0..6, bit7 MU report skips SU state',
                       'mode_GI_BW': 'byte5 mode0..1, GI_LTF2..4, bandwidth5..6',
                       'scope': 'original host parser accepts supplied mode/rate/GI values; not proof all combinations are valid PHY rates'},
          'records': records}
(root / 'manifests/radio-stock-RA-register.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'register_vectors': len(records), 'feedback_vectors': feedback_vectors,
                  'first': records[0], 'last': records[-1]}, indent=2))
