#!/usr/bin/env python3
"""Verify native radio backend and inputs in the selected final FIT."""
import argparse
import hashlib
import json
from pathlib import Path
from fit_payload import embedded_rootfs, fit_images
from radio_inputs import NativeRadioFormat

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('payload', type=Path)
parser.add_argument('--manifest', type=Path, default=root / 'artifacts/wifi-runtime-manifest.json')
args = parser.parse_args()
fit = args.payload / 'ram-pcie/image.itb'
selected = fit_images(fit)
assert b'native AP backend chip=' in selected['kernel'], 'Native AP PCI backend missing from selected FIT kernel'
assert b'firmware_state=validated' in selected['kernel'] and b'transport=pending mac80211=pending' in selected['kernel'], 'Native AP backend stage absent'
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
    report = {'FIT_sha256': hashlib.sha256(fit.read_bytes()).hexdigest(),
              'verification_entry': 'Final PCIe FIT selected kernel initramfs',
              'original_radio_firmware_files': len(expected['firmware']),
              'selected_RFE54_parameter_files': len(expected['parameters']),
              'all_bytes_match_prepared_board_inputs': True,
              'native_AP_PCI_backend_present_in_selected_kernel': True,
              'firmware_transport_implemented': False, 'mac80211_implemented': False,
              'initramfs_members': len(members), 'radio_hardware_verified': False}
    (args.payload / 'wifi-input-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
finally:
    codec.close()
