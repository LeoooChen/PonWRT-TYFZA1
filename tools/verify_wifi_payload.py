#!/usr/bin/env python3
"""Verify native radio backend and inputs in the selected final FIT."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from fit_payload import embedded_rootfs, fit_images
from radio_inputs import NativeRadioFormat

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('payload', type=Path)
parser.add_argument('--manifest', type=Path, default=root / 'artifacts/wifi-runtime-manifest.json')
parser.add_argument('--kernel-elf', type=Path, help='Compare linked native radio code with the final FIT kernel')
parser.add_argument('--require-wifi-management', action='store_true', help='Verify the packaged UCI/netifd/wpad runtime')
args = parser.parse_args()
fit = args.payload / 'ram-pcie/image.itb'
selected = fit_images(fit)
assert b'native AP backend chip=' in selected['kernel'], 'Native AP PCI backend missing from selected FIT kernel'
assert b'transport=channel12 packets=' in selected['kernel'], 'Native AP channel12 transport missing from selected FIT kernel'
members, start, end = embedded_rootfs(selected['kernel'])
expected = json.loads(args.manifest.read_text())
codec = NativeRadioFormat()
try:
    for item in expected['firmware']:
        data = members[item['path']]['data']
        assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path']
        image = codec.parse(data)
        assert image.cut == item['firmware_cut'] and list(image.version) == item['version']
        assert len(codec.header(data, image)) == item['download_header_bytes']
    for item in expected['parameters']:
        assert hashlib.sha256(members[item['path']]['data']).hexdigest() == item['sha256'], item['path']
    for item in expected.get('calibration', []):
        assert hashlib.sha256(members[item['path']]['data']).hexdigest() == item['sha256'], item['path']
    for item in expected.get('NCTL', []):
        assert hashlib.sha256(members[item['path']]['data']).hexdigest() == item['sha256'], item['path']
    if b'handed_to_mac80211=' in selected['kernel']:
        for item in expected['regulatory_database']:
            assert hashlib.sha256(members[item['path']]['data']).hexdigest() == item['sha256'], item['path']
    report = {'FIT_sha256': hashlib.sha256(fit.read_bytes()).hexdigest(),
              'verification_entry': 'Final PCIe FIT selected kernel initramfs',
              'original_radio_firmware_files': len(expected['firmware']),
              'selected_RFE54_parameter_files': len(expected['parameters']),
              'board_flash_calibration_files': len(expected.get('calibration', [])),
              'original_NCTL_tables': len(expected.get('NCTL', [])),
              'released_regulatory_database_and_signature': len(expected.get('regulatory_database', [])),
              'all_bytes_match_prepared_board_inputs': True,
              'native_AP_PCI_backend_present_in_selected_kernel': True,
              'firmware_transport_implemented': True,
              'native_mac80211_monitor_backend_present': b'handed_to_mac80211=' in selected['kernel'],
              'full_AP_operations_implemented': False,
              'initramfs_members': len(members), 'radio_hardware_verified': False}
    if args.require_wifi_management:
        paths = ('usr/sbin/wpad', 'sbin/wifi', 'lib/netifd/wireless/mac80211.sh',
                 'usr/share/hostap/hostapd.uc', 'usr/share/acl.d/wpad_acl.json',
                 'usr/lib/ucode/nl80211.so', 'usr/lib/ucode/rtnl.so')
        assert all(path in members for path in paths), 'OpenWrt Wi-Fi management runtime missing'
        assert b'vht_oper_chwidth' in members['usr/sbin/wpad']['data'], 'wpad VHT parser missing'
        assert b'he_oper_chwidth' in members['usr/sbin/wpad']['data'], 'wpad HE parser missing'
        report['OpenWrt_wifi_management'] = [{
            'path': path, 'SHA256': hashlib.sha256(members[path]['data']).hexdigest()} for path in paths]
    if args.kernel_elf is not None:
        sys.path.insert(0, str(root / 'analysis/python-deps'))
        from elftools.elf.elffile import ELFFile
        functions = []
        with args.kernel_elf.open('rb') as stream:
            elf = ELFFile(stream)
            table = elf.get_section_by_name('.symtab')
            base = table.get_symbol_by_name('_text')[0]['st_value']
            for name in ('rtl_ax_probe', 'rtl_ax_h2c_send', 'rtl_ax_wait_firmware', 'rtl_ax_shutdown',
                         'rtl_ax_receive_configure', 'rtl_ax_ap_conf_tx', 'rtl_ax_monitor_tx',
                         'rtl_ax_ampdu_action', 'rtl_ax_station_tables', 'rtl_ax_tx_submit', 'rtl_ax_tx_report'):
                symbol = table.get_symbol_by_name(name)[0]
                section = elf.get_section(symbol['st_shndx'])
                start = symbol['st_value'] - section['sh_addr']
                code = section.data()[start:start + symbol['st_size']]
                offset = symbol['st_value'] - base
                assert code and selected['kernel'][offset:offset + len(code)] == code, name
                functions.append({'function': name, 'selected_FIT_kernel_offset': offset,
                                  'code_bytes': len(code), 'SHA256': hashlib.sha256(code).hexdigest()})
        report['actual_radio_code_matches_linked_ELF'] = functions
    (args.payload / 'wifi-input-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
finally:
    codec.close()
