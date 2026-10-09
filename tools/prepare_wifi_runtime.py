#!/usr/bin/env python3
"""Install original AP firmware and the stock base-then-FEM RF input layers."""
import argparse
import hashlib
import json
import re
import sys
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from radio_inputs import NativeRadioFormat

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
parser.add_argument('--study', type=Path)
parser.add_argument('--manifest', type=Path, default=root / 'artifacts/wifi-runtime-manifest.json')
parser.add_argument('--openwrt-tree', type=Path, default=root / 'sources/openwrt')
parser.add_argument('--hardware-settings', type=Path,
                    default=root / 'evidence/pre-disassembly/backups/2026-10-07/working-dhcp/current-hs.xml')
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
    calibration = []
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
    hs_data = args.hardware_settings.read_bytes()
    hs = {item.attrib['Name']: item.attrib['Value']
          for item in ET.fromstring(hs_data).iter('Value')}
    gain_keys = ('HW_RX_GAINGAP_2G_CCK_AB', 'HW_RX_GAINGAP_2G_OFDM_AB',
                 'HW_RX_GAINGAP_5GL_AB', 'HW_RX_GAINGAP_5GM_AB', 'HW_RX_GAINGAP_5GH_AB')
    for family, band, fem in (('rtl8852ce', '5GHz', 'RTK66587_BPF'), ('rtl8192xbe', '2.4GHz', 'RTK66287')):
        wlan = 0 if family == 'rtl8852ce' else 1
        # Actual original normal gain calls query the flash shadow,not the
        # physicalOTP. Preserve the board's exported2G/5G gain/TSSI inputs.
        data = bytearray(b'TYFC0002')
        data.extend(int(hs[key]) for key in gain_keys)
        data.extend(int(hs[f'HW_THERMAL_{path}_WLAN{wlan}']) for path in ('A', 'B'))
        data.append(int(hs[f'HW_XCAP_WLAN{wlan}_AX']))
        for prefix, count in (('HW_2G_CCK_TSSI_', 6), ('HW_2G_BW40_1S_TSSI_', 5),
                              ('HW_5G_BW40_1S_TSSI_', 14)):
            for path in ('A', 'B'):
                values = bytes(int(value, 16) for value in hs[prefix + path].split(','))
                if len(values) != count:
                    raise ValueError(f'Unexpected original calibration length: {prefix}{path}')
                data.extend(values)
        wlan_address = bytes.fromhex(hs['WLAN_MAC_ADDR'])
        if len(wlan_address) != 6 or wlan_address[0] & 1:
            raise ValueError('Original HS WLAN_MAC_ADDR is not a unicast MAC address')
        data.extend(wlan_address)
        relative = Path('lib/firmware/realtek/tyfza1') / family / 'flash-calibration.bin'
        destination = args.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        calibration.append({'path': str(relative), 'bytes': len(data),
                            'sha256': hashlib.sha256(data).hexdigest(),
                            'hardware_settings_sha256': hashlib.sha256(hs_data).hexdigest(),
                            'wlan': wlan, 'source': 'original HS flash gain,thermal,xtal,TSSI and WLAN_MAC_ADDR fields',
                            'physical_OTP_is_separate': True})
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
    report = {'study': str(args.study.relative_to(root)), 'native_format_header_sha256':
              hashlib.sha256((root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/fw.h').read_bytes()).hexdigest(),
              'firmware': firmware, 'parameters': parameters, 'calibration': calibration, 'NCTL': nctl,
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
