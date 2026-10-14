#!/usr/bin/env python3
"""Install original AP firmware and the stock base-then-FEM RF input layers."""
import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
from pathlib import Path
from radio_inputs import NativeRadioFormat

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
parser.add_argument('--study', type=Path, default=os.environ.get('TYFZA1_WIFI_STUDY'))
parser.add_argument('--manifest', type=Path, default=root / 'artifacts/wifi-runtime-manifest.json')
parser.add_argument('--openwrt-tree', type=Path, default=root / 'sources/openwrt')
args = parser.parse_args()
if args.study is None:
    args.study = root / json.loads((root / 'manifests/wireless-study-current.json').read_text())['snapshot']
saved = json.loads((args.study / 'evidence/derived/stock-wifi-firmware-manifest.json').read_text())
codec = NativeRadioFormat()
firmware = []
try:
    package = (args.openwrt_tree / 'package/firmware/wireless-regdb/Makefile').read_text()
    version = re.search(r'^PKG_VERSION:=(\S+)', package, re.M).group(1)
    archive_hash = re.search(r'^PKG_HASH:=(\S+)', package, re.M).group(1)
    archive = args.openwrt_tree / 'dl' / f'wireless-regdb-{version}.tar.xz'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash:
        raise ValueError('Wireless regulatory release differs from the pinned OpenWrt package')
    regulatory = []
    with tarfile.open(archive) as release:
        for name in ('regulatory.db', 'regulatory.db.p7s'):
            data = release.extractfile(f'wireless-regdb-{version}/{name}').read()
            relative = Path('lib/firmware') / name
            destination = args.output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            regulatory.append({'path': str(relative), 'bytes': len(data),
                               'sha256': hashlib.sha256(data).hexdigest(),
                               'release': version, 'archive_sha256': archive_hash})
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
    # NCTL constants from this board's host ELF are device microprogram
    # input data. The native loader never loads the original host module.
    sys.path.insert(0, str(root / 'analysis/python-deps'))
    from elftools.elf.elffile import ELFFile
    nctl = []
    nctl_inputs = json.loads((root / 'manifests/radio-stock-rf-dm.json').read_text())
    host_source = args.study / 'evidence/stock/modules/rtk_wifi6.ko'
    assert hashlib.sha256(host_source.read_bytes()).hexdigest() == nctl_inputs['stock_module_sha256']
    with host_source.open('rb') as stream:
        elf = ELFFile(stream)
        symbols = elf.get_section_by_name('.symtab')
        for item in nctl_inputs['NCTL']['tables']:
            symbol = symbols.get_symbol_by_name(item['symbol'])[0]
            section = elf.get_section(symbol['st_shndx'])
            data = section.data()[symbol['st_value']:symbol['st_value'] + symbol['st_size']]
            assert len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256']
            relative = Path('lib/firmware/realtek/tyfza1') / (item['symbol'] + '.bin')
            destination = args.output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            nctl.append({'path': str(relative), 'bytes': len(data), 'pairs': item['pairs'],
                         'sha256': item['sha256'], 'source_symbol': item['symbol'],
                         'source_host_sha256': nctl_inputs['stock_module_sha256']})
    for family, band, fem in (('rtl8852ce', '5GHz', 'RTK66587_BPF'), ('rtl8192xbe', '2.4GHz', 'RTK66287')):
        # Per-unit HS data is loaded from ubi_Config on the device at boot.
        stale = args.output / 'lib/firmware/realtek/tyfza1' / family / 'flash-calibration.bin'
        stale.unlink(missing_ok=True)
        for stage, subdirectory in (('base', Path()), ('FEM-extension', Path(fem))):
            relative = Path('realtek/tyfza1') / family / 'RFE54' / subdirectory
            source = args.study / 'evidence/stock/radio-parameters' / family / 'RFE54' / subdirectory
            files = sorted(source.glob('*.txt'))
            assert {'RadioA.txt', 'RadioB.txt', 'PHY_REG.txt', 'TXPWR_LMT.txt'} <= {f.name for f in files}
            target = args.output / 'lib/firmware' / relative
            target.mkdir(parents=True, exist_ok=True)
            for file in files:
                data = file.read_bytes()
                original_sha256 = hashlib.sha256(data).hexdigest()
                corrections = []
                # The board's RU106/2T6GHz LPI row declares8 regulations,
                # but its PShape row has7 zeros. The other fiveRU rows use8
                # zeros. Keep the source evidence intact and repair its
                # missing zero explicitly in the installed parameter file.
                if original_sha256 == '7b63295122bb38ac76a62f052246525f094d17b48be0176c13d19da5ea2dc38f':
                    data = data.replace(b'PShape\t0\t0\t0\t0\t0\t\t0\t0',
                                        b'PShape\t0\t0\t0\t0\t0\t0\t0\t0')
                    corrections.append('RU106/2T6GHzLPI: complete8-column zeroPShape row')
                if original_sha256 == 'dd73d905c7841f2220d3a6b0ba90a6b48cf8456ca6886837ed567d12e39beff3':
                    data = data.replace(b'PShape\t\t\t\t\t', b'PShape\t0\t\t\t\t')
                    corrections.append('RU106/2T6GHzVLP: restore single zeroPShape entry')
                (target / file.name).write_bytes(data)
                parameters.append({'path': str(Path('lib/firmware') / relative / file.name),
                                   'band': band, 'FEM': fem, 'RFE': 54, 'load_stage': stage,
                                   'original_sha256': original_sha256,
                                   'sha256': hashlib.sha256(data).hexdigest(), 'corrections': corrections})
    # The original file loader assigns base curves then overwrites them
    # with the selected FEM layer. Keep the actual four2G ALL curves.
    track_source = args.study / 'evidence/stock/radio-parameters/rtl8192xbe/RFE54'
    curves = {}
    for layer in (track_source, track_source / 'RTK66287'):
        source = layer / 'TXPWR_TrackTSSI.txt'
        for path in ('A', 'B'):
            for sign in ('+', '-'):
                pattern = r'\[2G\]\[' + path + r'\]\[' + re.escape(sign) + r'\]\[ALL\]\s*=\s*\{([^}]+)\}'
                values = bytes(int(value) & 255 for value in re.search(pattern, source.read_text())[1].split(','))
                if len(values) != 30:
                    raise ValueError('Unexpected original TSSI thermal curve length')
                curves[(path, sign)] = values
    data = b''.join(curves[(path, sign)] for path in ('A', 'B') for sign in ('+', '-'))
    relative = Path('lib/firmware/realtek/tyfza1/rtl8192xbe/tssi-thermal-2g.bin')
    (args.output / relative).write_bytes(data)
    parameters.append({'path': str(relative), 'band': '2.4GHz', 'FEM': 'RTK66287', 'RFE': 54,
                       'load_stage': 'derived-base-then-FEM-thermal', 'bytes': len(data),
                       'source': 'original TXPWR_TrackTSSI.txt 2G A/B +/- ALL rows',
                       'sha256': hashlib.sha256(data).hexdigest(),
                       'selected_FEM_sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    track_source = args.study / 'evidence/stock/radio-parameters/rtl8852ce/RFE54'
    curves = {}
    for layer in (track_source, track_source / 'RTK66587_BPF'):
        source = layer / 'TXPWR_TrackTSSI.txt'
        for group in range(3):
            for path in ('A', 'B'):
                for sign in ('+', '-'):
                    pattern = r'\[5G\]\[' + path + r'\]\[' + re.escape(sign) + r'\]\[ALL\]\[' + str(group) + r'\]\s*=\s*\{([^}]+)\}'
                    values = bytes(int(value) & 255 for value in re.search(pattern, source.read_text())[1].split(','))
                    if len(values) != 30:
                        raise ValueError('Unexpected original C TSSI thermal curve length')
                    curves[(group, path, sign)] = values
    data = b''.join(curves[(group, path, sign)] for group in range(3)
                    for path in ('A', 'B') for sign in ('+', '-'))
    relative = Path('lib/firmware/realtek/tyfza1/rtl8852ce/tssi-thermal-5g.bin')
    (args.output / relative).write_bytes(data)
    parameters.append({'path': str(relative), 'band': '5GHz', 'FEM': 'RTK66587_BPF', 'RFE': 54,
                       'load_stage': 'derived-base-then-FEM-thermal', 'bytes': len(data),
                       'source': 'original TXPWR_TrackTSSI.txt 5G A/B +/- groups 0,1,2',
                       'sha256': hashlib.sha256(data).hexdigest(),
                       'selected_FEM_sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    report = {'study': str(args.study), 'native_format_header_sha256':
              hashlib.sha256((root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/fw.h').read_bytes()).hexdigest(),
              'firmware': firmware, 'parameters': parameters, 'calibration': [],
              'calibration_source': 'device ubi0:ubi_Config, generated before kmodloader', 'NCTL': nctl,
              'regulatory_database': regulatory,
              'RF_load_order': ['base', 'FEM-extension'],
              'RF_tables_applied_by_driver': False, 'radio_driver_available': False}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'radio_firmwares': len(firmware), 'selected_RF_parameter_files': len(parameters),
                      'NCTL_tables': len(nctl),
                      'output': str(args.output), 'native_driver_in_progress': True}, indent=2))
finally:
    codec.close()
