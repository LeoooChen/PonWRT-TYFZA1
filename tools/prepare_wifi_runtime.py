#!/usr/bin/env python3
"""Install original radio firmware data and the board's selected RFE54 tables."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from radio_inputs import NativeRadioFormat

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
parser.add_argument('--study', type=Path)
parser.add_argument('--manifest', type=Path, default=root / 'artifacts/wifi-runtime-manifest.json')
args = parser.parse_args()
if args.study is None:
    args.study = root / json.loads((root / 'manifests/wireless-study-current.json').read_text())['snapshot']
saved = json.loads((args.study / 'evidence/derived/stock-wifi-firmware-manifest.json').read_text())
codec = NativeRadioFormat()
firmware = []
try:
    for mac, family in ((2, '8852c'), (3, '8192xb')):
        for cv in (0, 1):
            name = f'array_{family}_u{cv + 1}_ap'
            record = saved['firmwares'][name]
            source = args.study / record['extracted_path']
            data = source.read_bytes()
            assert hashlib.sha256(data).hexdigest() == record['sha256']
            image = codec.parse(data)
            assert image.cut == cv + 1 and image.chip >> 8 == (0x8852 if mac == 2 else 0x8192)
            assert image.section_count == len(record['sections'])
            for section, expected in zip(image.sections, record['sections']):
                assert (section.address, section.offset, section.bytes) == (
                    int(expected['device_load_address'], 16), expected['offset'], expected['stored_size'])
            relative = Path(codec.library.name(mac, cv).decode())
            destination = args.output / 'lib/firmware' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            header = codec.header(data, image)
            firmware.append({'path': str(Path('lib/firmware') / relative), 'bytes': len(data),
                             'sha256': record['sha256'], 'MAC_chip_id': mac, 'host_cv': cv,
                             'firmware_cut': image.cut, 'version': list(image.version),
                             'download_header_bytes': len(header), 'section_packet_bytes': 2020})
    parameters = []
    for family, band, fem in (('rtl8852ce', '5GHz', 'RTK66587_BPF'), ('rtl8192xbe', '2.4GHz', 'RTK66287')):
        relative = Path('realtek/tyfza1') / family / 'RFE54' / fem
        source = args.study / 'evidence/stock/radio-parameters' / family / 'RFE54' / fem
        files = sorted(source.glob('*.txt'))
        assert {'RadioA.txt', 'RadioB.txt', 'PHY_REG.txt', 'TXPWR_LMT.txt'} <= {f.name for f in files}
        target = args.output / 'lib/firmware' / relative
        target.mkdir(parents=True, exist_ok=True)
        for file in files:
            shutil.copyfile(file, target / file.name)
            parameters.append({'path': str(Path('lib/firmware') / relative / file.name),
                               'band': band, 'FEM': fem, 'RFE': 54,
                               'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    report = {'study': str(args.study.relative_to(root)), 'native_format_header_sha256':
              hashlib.sha256((root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtw89/rtl_ax_ap_fw.h').read_bytes()).hexdigest(),
              'firmware': firmware, 'parameters': parameters, 'radio_driver_available': False}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'radio_firmwares': len(firmware), 'selected_RF_parameter_files': len(parameters),
                      'output': str(args.output), 'native_driver_in_progress': True}, indent=2))
finally:
    codec.close()
