#!/usr/bin/env python3
"""Extract TYFZA1 PCIe topology and radio inputs from the saved board evidence."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import sys
from zoneinfo import ZoneInfo

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--study', type=Path, default=root / 'evidence/wireless-study-20261008')
parser.add_argument('--output', type=Path, default=root / 'manifests/wifi-board-inputs.json')
args = parser.parse_args()

from stock_dtb import banked_gpio, cells, fdt_nodes, names

dtb = args.study / 'evidence/stock/live-fit.dtb'
nodes = fdt_nodes(dtb.read_bytes())
handles = {cells(properties['phandle'])[0]: (path, properties)
           for path, properties in nodes.items() if 'phandle' in properties}
hosts = []
for path, props in nodes.items():
    if props.get('compatible') != b'cortina,venus-pcie\0':
        continue
    reg = cells(props['reg'])
    resource_names = names(props['reg-names'])
    assert len(reg) == len(resource_names) * 4
    resources = {name: {'address': hex((reg[i * 4] << 32) | reg[i * 4 + 1]),
                        'bytes': (reg[i * 4 + 2] << 32) | reg[i * 4 + 3]}
                 for i, name in enumerate(resource_names)}
    reset_refs = cells(props['resets'])
    reset_names = names(props['reset-names'])
    resets, cursor = {}, 0
    for name in reset_names:
        provider_path, provider = handles[reset_refs[cursor]]
        count = cells(provider['#reset-cells'])[0]
        item = {'provider': provider_path, 'arguments': reset_refs[cursor + 1:cursor + 1 + count]}
        if 'reg' in provider:
            item['provider_reg'] = [hex(v) for v in cells(provider['reg'])]
        if 'gpios' in provider:
            gpios = cells(provider['gpios'])
            item['endpoint_GPIO'] = banked_gpio(handles, gpios,
                enabled=names(provider.get('status', b'okay'))[0] != 'disabled')
        resets[name] = item
        cursor += count + 1
    assert cursor == len(reset_refs)
    clock_path, clock = handles[cells(props['clocks'])[0]]
    atu_path, atu = handles[cells(props['iommus'])[0]]
    serdes = {}
    for cut in ('A', 'B'):
        data = props['serdes-cfg-data' + cut]
        sequence = cells(data)
        assert len(sequence) % 2 == 0
        serdes[cut] = {'version': names(props['serdes-cfg-ver' + cut])[0],
                       'register_pairs': len(sequence) // 2, 'sha256': hashlib.sha256(data).hexdigest()}
    hosts.append({'node': path, 'index': cells(props['id'])[0],
                  'status': names(props.get('status', b'okay'))[0],
                  'lanes': cells(props['num-lanes'])[0], 'resources': resources,
                  'interrupts_GIC_cells': cells(props['interrupts']), 'resets': resets,
                  'clock': {'provider': clock_path, 'gate_shift': int.from_bytes(clock['operate-shift'], 'big')},
                  'SoC_ATU': {'provider': atu_path, 'reg_cells': [hex(v) for v in cells(atu['reg'])],
                              'coherent_hi_addr': cells(atu['coherent-hi-addr'])[0]},
                  'dma_coherent': 'dma-coherent' in props, 'SerDes_profiles': serdes})
assert [h['status'] for h in sorted(hosts, key=lambda h: h['index'])] == ['okay', 'okay', 'disabled']

module = args.study / 'evidence/stock/modules/rtk_wifi6.ko'
with module.open('rb') as stream:
    elf = ELFFile(stream)
    symbol = elf.get_section_by_name('.symtab').get_symbol_by_name('__mod_pci__rtw_pci_id_tbl_device_table')[0]
    section = elf.get_section(symbol['st_shndx']).data()
    table = section[symbol['st_value']:symbol['st_value'] + symbol['st_size']]
    assert len(table) % 32 == 0
    matches = []
    for offset in range(0, len(table), 32):
        row = struct.unpack_from('<6IQ', table, offset)
        if not any(row):
            break
        matches.append(dict(zip(('vendor', 'device', 'subvendor', 'subdevice', 'class', 'class_mask', 'driver_data'),
                                [hex(v) for v in row])))
    for name,item in json.loads((args.study / 'evidence/derived/stock-wifi-firmware-manifest.json').read_text())['firmwares'].items():
        sym=elf.get_section_by_name('.symtab').get_symbol_by_name(name)[0]
        raw=elf.get_section(sym['st_shndx']).data()[sym['st_value']:sym['st_value']+sym['st_size']]
        assert len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256']

fw = json.loads((args.study / 'evidence/derived/stock-wifi-firmware-manifest.json').read_text())
report = {'extracted_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
          'stock_DTB_sha256': hashlib.sha256(dtb.read_bytes()).hexdigest(),
          'stock_wireless_module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'PCIe_hosts': hosts, 'compiled_PCI_matches': matches,
          'live_PCI_identity': 'Await native PCIe enumeration; compiled matches are not board enumeration',
          'firmware_candidates': {name: {key: item[key] for key in
                                       ('bytes', 'sha256', 'cut_id', 'firmware_chip_id', 'version', 'header_length')}
                                  for name, item in fw['firmwares'].items()},
          'RFE_FEM_selection': 'Read cut/efuse/board subtype and initialization overrides before choosing RF tables',
          'native_driver_order': 'docs/wifi-port-order.md', 'hardware_verified': False}
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'PCIe_hosts': [{'index': h['index'], 'status': h['status']} for h in hosts],
                  'compiled_PCI_matches': matches, 'firmware_candidates': list(report['firmware_candidates']),
                  'output': str(args.output)}, ensure_ascii=False, indent=2))
