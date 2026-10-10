#!/usr/bin/env python3
"""Extract AP port operations by executing the original TYFZA1 MAC functions.

The IO boundary records register operations; its TSF clock advances only with
the original platform delay callback. H2C transport records original payloads.
This is an ELF trace, not an RF or AP association result.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle
from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                UC_ARM64_REG_X6, UC_ARM64_REG_X7,
                                UC_ARM64_REG_LR)

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--role-address', default='020000961701')
parser.add_argument('--output', type=Path, default=root / 'manifests/radio-stock-AP-port.json')
parser.add_argument('--io-snapshot', type=Path)
args = parser.parse_args()
io_snapshot = json.loads(args.io_snapshot.read_text()) if args.io_snapshot else {}
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['mac_port_init', 'mac_port_cfg', '_get_port_cfg', 'is_curr_dbcc',
         'is_chip_id', 'check_mac_en', 'dly_port_us', '_patch_tbtt_shift_setval',
         'chk_patch_tbtt_shift_setval', 'mac_wde_pkt_drop', 'hiq_drop_ctrl',
         'h2c_pkt_drop', 'chk_patch_cmac_hiq_drop',
         '_hal_stainfo_to_macrinfo', '_rtype_to_mac_nettype', 'mac_add_role',
         'role_info_valid', 'sec_info_init', 'role_init', 'role_info_init',
         'get_addr_cam_size', 'set_macid_pause', 'macid_pause', 'mac_fw_role_maintain',
         'mac_h2c_join_info', 'mac_upd_addr_cam', 'init_addr_cam_info',
         'find_avail_addr_cam_entry', 'find_avail_bssid_cam_entry',
         'mac_role_srch', 'mac_role_srch_by_addr_cam', 'mac_role_srch_by_bssid',
         'get_set_bits_of_msk', 'fill_addr_cam_info', 'fill_bssid_cam_info',
         'set_role_bss_clr', '_change_role', '_role_srch',
         'mac_remove_role', '_remove_role', 'role_return', 'sec_info_deinit',
         'change_addr_cam_info']
adapter, ops, io_ops, platform, params, pinfo = [0x6000000 + i * 0x10000 for i in range(6)]
packet, payload = 0x6060000, 0x6070000
cfg_names = ['FUNC_SW', 'TX_SW', 'TX_RPT', 'RX_SW', 'RX_RPT', 'RX_SYNC',
             'BCN_PRCT', 'TBTT_AGG', 'TBTT_SHIFT', 'RST_TSF', 'RST_TPR',
             'BCAID', 'HIQ_WIN', 'HIQ_DTIM', 'HIQ_NOLIMIT', 'NET_TYPE',
             'BCN_INTV', 'BCN_SETUP_TIME', 'BCN_HOLD_TIME', 'MBSSID_EN',
             'BCN_ERLY', 'BCN_MASK_AREA', 'TBTT_ERLY', 'BSS_CLR', 'MBSSID_NUM',
             'BCN_DRP_ALL']
state = {}


def callback(name, args):
    if name.startswith('read'):
        width = int(name[4:]) // 8
        address = args[1]
        if address in tsf_addresses:
            return state['time_us'] & ((1 << (width * 8)) - 1)
        return sum(state['registers'].get(address + i, 0) << (8 * i) for i in range(width))
    if name.startswith('write'):
        width = int(name[5:]) // 8
        address, value = args[1:3]
        before = sum(state['registers'].get(address + i, 0) << (8 * i) for i in range(width))
        value &= (1 << (width * 8)) - 1
        for i in range(width):
            state['registers'][address + i] = value >> (8 * i) & 255
        state['operations'].append({'op': name, 'address': hex(address),
            'before': hex(before), 'value': hex(value), 'phase': state['phase'],
            'original_return_PC': hex(oracle.uc.reg_read(UC_ARM64_REG_LR))})
        return 0
    if name == 'delay_us':
        state['time_us'] += args[1]
        state['operations'].append({'op': name, 'us': args[1]})
        return 0
    if name == 'memset':
        oracle.uc.mem_write(args[1], bytes([args[2] & 255]) * args[3])
        return 0
    if name in ('memcpy', '_rtw_memcpy'):
        oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[0]
    if name == 'platform_memcpy':
        oracle.uc.mem_write(args[1], bytes(oracle.uc.mem_read(args[2], args[3])))
        return 0
    if name in ('memcmp', 'platform_memcmp'):
        a, b, n = args[:3] if name == 'memcmp' else args[1:4]
        first, second = bytes(oracle.uc.mem_read(a, n)), bytes(oracle.uc.mem_read(b, n))
        return (first > second) - (first < second)
    if name in ('log', 'printk', 'h2cb_free', 'h2c_end_flow', 'h2c_pkt_build_txd', 'lock', 'unlock'):
        return 0
    if name == 'h2cb_alloc':
        oracle.uc.mem_write(packet, bytes(256))
        return packet
    if name == 'h2cb_put':
        state['payload_bytes'] = args[1]
        oracle.uc.mem_write(payload, bytes(512))
        return payload
    if name == 'h2c_pkt_set_hdr':
        extra = [oracle.uc.reg_read(r) for r in (UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                               UC_ARM64_REG_X6, UC_ARM64_REG_X7)]
        state['operations'].append({'op': 'H2C', 'type': args[2], 'category': args[3],
            'class': extra[0], 'function': extra[1], 'receive_ACK': extra[2],
            'done_ACK': extra[3], 'payload': bytes(oracle.uc.mem_read(payload, state['payload_bytes'])).hex()})
        return 0
    if name == 'h2c_agg_enqueue':
        return 1  # select original immediate-send path, without host aggregation
    if name == 'platform_TX':
        return 0
    raise RuntimeError((name, args))


oracle = ModuleOracle(module, names, callback)
for address in (adapter, ops, io_ops, platform, params, pinfo, packet, payload):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(adapter, ops)
oracle.word(ops, io_ops)
oracle.word(ops + 888, oracle.functions['mac_wde_pkt_drop'])
oracle.word(adapter + 24, platform)
oracle.word(adapter + 1016, pinfo)
for offset, name in [(0, 'read8'), (8, 'write8'), (16, 'read16'), (24, 'write16'),
                     (32, 'read32'), (40, 'write32')]:
    oracle.word(io_ops + offset, oracle.callback(name))
for offset, name in [(48, 'platform_TX'), (88, 'platform_memcpy'), (96, 'memset'),
                     (104, 'platform_memcmp'), (112, 'delay_us'),
                     (160, 'lock'), (168, 'unlock'), (176, 'log')]:
    oracle.word(platform + offset, oracle.callback(name))
# check_mac_en's actual power/DMAC/CMAC0 state fields after native MAC startup.
oracle.uc.mem_write(adapter + 32, b'\x01\x04')
oracle.uc.mem_write(adapter + 67, b'\x01\x01\x00')
with module.open('rb') as stream:
    elf = ELFFile(stream)
    rodata_index = next(i for i, s in enumerate(elf.iter_sections()) if s.name == '.rodata')
tsf_addresses = set(oracle.unpack(oracle.sections[rodata_index] + 0x1e178 + 0x160, '10I'))


def record_cfg(uc, address, size, user_data):
    if address != oracle.functions['mac_port_cfg']:
        return
    cfg = uc.reg_read(UC_ARM64_REG_X1)
    mbid, value, port, band = oracle.unpack(uc.reg_read(UC_ARM64_REG_X2), 'IIBB')
    state['phase'] = cfg_names[cfg]
    state['operations'].append({'op': 'port_cfg', 'name': state['phase'],
                                'mbssid_index': mbid, 'value': value, 'port': port, 'band': band})


oracle.uc.hook_add(UC_HOOK_CODE, record_cfg)
programs = []
for chip in ('8852c', '8192xb'):
    for mbssid in (0, 7):
        state.clear()
        state.update(registers={}, operations=[], time_us=0, phase='port_init')
        for address, value in io_snapshot.get(chip, {}).items():
            address, value = int(address, 16), int(value, 16)
            for i in range(4):
                state['registers'][address + i] = value >> (8 * i) & 255
        oracle.word(adapter + 88, oracle.symbols['mac' + chip + '_hw_info'][0])
        oracle.uc.mem_write(pinfo, bytes(64))
        # Same AP/band0/port0 role as original rtw_hal_mac_port_init;
        # rtw_phl_wifi_role_alloc@13af14..13af28 initializes AP interval100,
        # HIQ window50. rtw_hal_mac_port_init@1aa14c copies role+1268.
        oracle.uc.mem_write(params, struct.pack('<IIIBBBxH2xI', 0, 0, 3, 1, mbssid, 0, 100, 50))
        status = oracle.call('mac_port_init', adapter, params)
        if status:
            raise RuntimeError((chip, mbssid, status, state['operations'][-6:]))
        programs.append({'chip': chip, 'band': 0, 'port': 0, 'MBSSID_count': mbssid,
            'interval_TU': 100, 'DTIM': 1, 'HIQ_window': 50, 'original_status': status,
            'original_port_state': bytes(oracle.uc.mem_read(pinfo, 3)).hex(),
            'operations': list(state['operations']),
            'final_registers': {hex(a): hex(sum(state['registers'].get(a + i, 0) << (8 * i) for i in range(4)))
                for a in (0xc400, 0xc404, 0xc408, 0xc40c, 0xc410, 0xc414, 0xc424,
                          0xc540, 0xc544, 0xc590, 0xc594, 0xc6a0, 0xca08)}})
role_programs = []
head, hal, hal_common, role, station, role_info = [0x6080000 + i * 0x10000 for i in range(6)]
for address in (head, hal, hal_common, role, station, role_info):
    oracle.uc.mem_map(address, 0x10000)
ap_address = bytes.fromhex(args.role_address.replace(':', ''))
for chip in ('8852c', '8192xb'):
    state.clear()
    state.update(registers={}, operations=[], time_us=0, phase='AP_self_role')
    oracle.word(adapter + 88, oracle.symbols['mac' + chip + '_hw_info'][0])
    oracle.word(adapter + 608, head)
    # Empty active list and one free role node, using original list layout.
    pool, node = head + 256, head + 512
    oracle.uc.mem_write(head, bytes(2048))
    oracle.word(head, head)
    oracle.word(head + 8, head)
    oracle.word(head + 16, pool)
    oracle.word(pool, node)
    oracle.word(pool + 8, node)
    oracle.uc.mem_write(pool + 24, struct.pack('<I', 1))
    oracle.word(node, pool)
    oracle.word(node + 8, pool)
    oracle.word(hal + 8, hal_common)
    oracle.word(station + 16, role)
    oracle.uc.mem_write(role + 8, struct.pack('<I', 2))  # original PHL AP enum
    oracle.uc.mem_write(role + 28, ap_address)
    oracle.uc.mem_write(station + 28, bytes(2))
    oracle.uc.mem_write(station + 30, ap_address)
    oracle.uc.mem_write(station + 2656, struct.pack('<Q', 1 << 20))
    oracle.uc.mem_write(role_info, bytes(512))
    oracle.call('_hal_stainfo_to_macrinfo', hal, station, role_info, 0, 0)
    raw_role = bytes(oracle.uc.mem_read(role_info, 62)).hex()
    status = oracle.call('mac_add_role', adapter, role_info)
    if status:
        raise RuntimeError((chip, 'AP_self_role', status, state['operations'][-6:]))
    role_programs.append({'chip': chip, 'scope': 'open legacy AP self-station, MACID0, SCC band0/port0, specified MAC input',
                         'role_address': ap_address.hex(),
                         'original_status': status, 'original_HAL_role_input': raw_role,
                         'operations': list(state['operations'])})
    peer_node = head + 1024
    oracle.word(pool, peer_node)
    oracle.word(pool + 8, peer_node)
    oracle.uc.mem_write(pool + 24, struct.pack('<I', 1))
    oracle.word(peer_node, pool)
    oracle.word(peer_node + 8, pool)
    peer_address = bytes.fromhex('020000961702')
    oracle.uc.mem_write(station + 28, struct.pack('<H', 1))
    oracle.uc.mem_write(station + 30, peer_address)
    oracle.uc.mem_write(role_info, bytes(512))
    oracle.call('_hal_stainfo_to_macrinfo', hal, station, role_info, 0, 0)
    for operation in ('create', 'connect', 'remove'):
        state['operations'] = []
        if operation == 'create':
            status = oracle.call('mac_add_role', adapter, role_info)
        elif operation == 'connect':
            oracle.uc.mem_write(role_info + 12, struct.pack('<II', 4, 0))
            oracle.uc.mem_write(role_info + 60, struct.pack('<H', 1))
            status = oracle.call('_change_role', adapter, role_info)
        else:
            status = oracle.call('mac_remove_role', adapter, 1)
        if status:
            raise RuntimeError((chip, operation, status, state['operations']))
        role_programs.append({'chip': chip, 'scope': 'open legacy AP peer MACID1, AID1, SCC band0/port0',
                             'operation': operation, 'role_address': ap_address.hex(),
                             'peer_address': peer_address.hex(), 'original_status': status,
                             'operations': list(state['operations'])})
report = {'scope': 'actual original ELF AP port configuration and HIQ-drop payloads; initial IO specified below, TSF advances with delays; not a board test',
    'initial_IO': str(args.io_snapshot) if args.io_snapshot else 'zero',
    'original_host_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
    'executed_original_functions': names,
    'transport_boundaries': ['H2C allocation/header/aggregation/TXD/platform send; payload construction executes original code'],
    'programs': programs, 'role_programs': role_programs}
target = args.output
target.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'output': str(target), 'programs': [
    {'chip': p['chip'], 'MBSSID_count': p['MBSSID_count'], 'operations': len(p['operations'])} for p in programs]}))
