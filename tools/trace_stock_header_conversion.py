#!/usr/bin/env python3
"""Execute actual C/XB header controls; distinguish DCTL from header-CAM support."""
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['mac_hdr_conv_en', 'mac_hdr_conv_tx_macid_en', 'mac_upd_shcut_mhdr',
         'is_chip_id', 'chk_patch_txamsdu_rls_wd_issue', 'mac_get_wp_offset_8852c',
         'mac_get_wp_offset_8192xb', 'mac_upd_dctl_info_8852c', 'mac_upd_dctl_info_8192xb']
adapter, platform, mac, registers, packet, payload = [0x6000000 + i * 0x10000 for i in range(6)]
register_value = 0
payload_size = 0


def callback(name, args):
    global register_value, payload_size
    if name in ('printk', 'h2cb_free', 'h2c_pkt_build_txd', 'platform_TX',
                'h2c_pkt_set_hdr', 'h2c_end_flow'):
        return 0
    if name == 'h2cb_alloc':
        return packet
    if name == 'h2cb_put':
        payload_size = args[1]
        oracle.uc.mem_write(payload, bytes(512))
        return payload
    if name == 'h2c_agg_enqueue':
        return 1
    if name == 'platform_memcpy':
        oracle.uc.mem_write(args[1], bytes(oracle.uc.mem_read(args[2], args[3])))
        return 0
    if name == 'read32':
        assert args[1] == 0x9b00
        return register_value
    if name == 'write32':
        assert args[1] == 0x9b00
        register_value = args[2]
        return 0
    raise RuntimeError((name, args))


oracle = ModuleOracle(module, names, callback)
for address in (adapter, platform, mac, registers, packet, payload):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(adapter, mac)
oracle.word(adapter + 24, platform)
oracle.word(mac, registers)
oracle.word(registers + 32, oracle.callback('read32'))
oracle.word(registers + 40, oracle.callback('write32'))
oracle.word(platform + 48, oracle.callback('platform_TX'))
oracle.word(platform + 88, oracle.callback('platform_memcpy'))
oracle.uc.mem_write(adapter + 33, b'\x04')
records = []
for chip in ('8852c', '8192xb'):
    hwinfo = oracle.symbols['mac' + chip + '_hw_info'][0]
    oracle.word(adapter + 88, hwinfo)
    oracle.word(mac + 272, oracle.functions['mac_upd_dctl_info_' + chip])
    register_value = 0x12340080
    assert oracle.call('mac_hdr_conv_en', adapter, 1) == 0
    assert register_value == 0x12340086
    assert oracle.call('mac_hdr_conv_en', adapter, 0) == 0
    assert register_value == 0x12340080
    assert oracle.call('mac_upd_shcut_mhdr', adapter, oracle.scratch, 1) == 82
    assert oracle.call('chk_patch_txamsdu_rls_wd_issue', adapter) == 0
    for macid in (1, 7, 127):
        for qos in (0, 1):
            header_units = 12 + qos
            config = 0x5a | qos << 8 | header_units << 9
            assert oracle.call('mac_hdr_conv_tx_macid_en', adapter, macid, config) == 0
            words = list(oracle.unpack(payload, '17I'))
            assert payload_size == 68
            assert words[0] == macid | 0x80
            assert words[1] == 0x5a | qos << 15
            assert words[5] == header_units
            assert words[9] == 0x80ff and words[13] == 0x3f
            records.append({'chip': chip, 'MACID': macid, 'QoS': bool(qos),
                            'header_bytes': header_units * 2, 'DCTL_words': words})
    for cipher, name in ((1, 'software'), (2, 'none'), (7, 'CCMP128')):
        for conversion in (1, 2, 3):
            oracle.uc.mem_write(oracle.scratch, struct.pack('<III', cipher, 1, conversion))
            assert oracle.call('mac_get_wp_offset_' + chip, adapter, oracle.scratch, oracle.scratch + 32) == 0
            records.append({'chip': chip, 'cipher': name, 'conversion_enum': conversion,
                            'HW_AMSDU': False, 'WP_offset_units8': oracle.unpack(oracle.scratch + 32, 'H')[0]})
report = {'scope': __doc__, 'global_header_control_bits': [1, 2],
          'C_XB_header_CAM_update_result': 82, 'register_IO_and_H2C_transport_mocked': True,
          'hardware_ETH_to_WLAN_verified': False, 'records': records}
(root / 'logs/radio-header-conversion-contract.json').write_text(json.dumps(report, indent=2) + '\n')
print('Original C/XB: global TX header processing and DCTL update succeed; header-CAM update is MACNOTSUP(82); 30 DCTL/WP vectors recorded')
