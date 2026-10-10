#!/usr/bin/env python3
"""Execute original TYFZA1 RX BA creation/deletion and its actual MAC callback."""
from pathlib import Path
import hashlib
import json
import struct
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['rtw_hal_start_ba_session', 'rtw_hal_stop_ba_session', '_hal_update_ba_cam',
         'rtw_hal_bacam_cfg', 'mac_bacam_info', 'mac_bacam_avl_std_entry_idx']
hal, sta, hsta, adapter, ops, platform, packet, payload = [0x6000000 + n * 0x10000 for n in range(8)]
commands = []
size = 0
buffer = 0

def callback(name, args):
    global size, buffer
    if name == '_rtw_memset':
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
        return args[0]
    if name in ('printk', 'h2cb_free', 'h2c_pkt_build_txd', 'platform_TX'):
        return 0
    if name == 'h2cb_alloc':
        buffer = args[1]
        return packet
    if name == 'h2cb_put':
        size = args[1]
        oracle.uc.mem_write(payload, bytes(128))
        return payload
    if name == 'h2c_pkt_set_hdr':
        extra = [oracle.uc.reg_read(r) for r in (UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7)]
        commands.append({'buffer': buffer, 'category': args[3], 'class': extra[0], 'function': extra[1],
                         'receive_ACK': extra[2], 'done_ACK': extra[3],
                         'payload': bytes(oracle.uc.mem_read(payload, size)).hex()})
        return 0
    if name == 'h2c_agg_enqueue':
        return 1
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, names, callback)
for address in (hal, sta, hsta, adapter, ops, platform, packet, payload):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(hal + 328, adapter)
oracle.word(sta + 2688, hsta)
oracle.word(adapter, ops)
oracle.word(adapter + 24, platform)
oracle.uc.mem_write(adapter + 33, b'\x04')
oracle.word(platform + 48, oracle.callback('platform_TX'))
records = []
for chip in ('8852c', '8192xb'):
    oracle.word(adapter + 88, oracle.symbols['mac' + chip + '_hw_info'][0])
    original_ops = oracle.symbols['mac' + chip + '_ops'][0]
    for offset, name in ((496, 'mac_bacam_avl_std_entry_idx'), (504, 'mac_bacam_info')):
        target = oracle.unpack(original_ops + offset, 'Q')[0]
        assert target == oracle.functions[name]
        oracle.word(ops + offset, target)
    for macid in (1, 2):
        oracle.uc.mem_write(hsta, bytes(256))
        oracle.uc.mem_write(sta + 28, struct.pack('<H', macid))
        for action, tid, seq in [('start', 0, 123), ('start', 6, 2047), ('stop', 0, 0), ('stop', 6, 0)]:
            commands.clear()
            if action == 'start':
                status = oracle.call('rtw_hal_start_ba_session', hal, sta, 1, 0, seq, 1, tid, 64)
            else:
                status = oracle.call('rtw_hal_stop_ba_session', hal, sta, tid)
            assert status == 0
            records.append({'chip': chip, 'MACID': macid, 'action': action, 'TID': tid, 'SSN': seq,
                            'buffer_size': 64, 'commands': list(commands),
                            'original_BA_control': bytes(oracle.uc.mem_read(hsta + 120, 5)).hex()})
    # The chip-specific MAC callback encodes the extended entry index in
    # word1. Record this ABI separately from the older HAL wrapper output.
    for index in (8, 9, 15):
        for valid in (False, True):
            oracle.uc.mem_write(sta, struct.pack('<II',
                int(valid) | 2 | (6 << 4 if valid else 0) | 1 << 8 | (123 << 20 if valid else 0),
                index << 28 | (1 << 8 if valid else 0)))
            commands.clear()
            assert oracle.call('mac_bacam_info', adapter, sta) == 0
            records.append({'chip':chip,'MACID':1,'action':'direct_MAC_encoder',
                            'valid':valid,'TID':6,'extended_entry_index':index,
                            'standard_entry_enabled':valid,'commands':list(commands)})
(root / 'manifests/radio-stock-BA-sessions.json').write_text(json.dumps({
    'scope': __doc__, 'original_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
    'executed_functions': names, 'records': records}, indent=2) + '\n')
for r in records:
    print(r['chip'], r['MACID'], r['action'], r['TID'], r['commands'])
