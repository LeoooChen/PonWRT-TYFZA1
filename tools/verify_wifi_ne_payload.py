#!/usr/bin/env python3
"""Verify linked NE/radio code and preserved private runtime in the final RAM FIT."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from fit_payload import fit_images, embedded_rootfs

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('payload', type=Path)
parser.add_argument('--runtime', type=Path)
parser.add_argument('--ram-variant', default='ram-pcie')
args = parser.parse_args()
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile
kernel = Path(subprocess.check_output(['python3', str(root / 'port/tools/project_paths.py'),
    '--kernel-directory', str(root / 'sources/openwrt')], text=True).strip())
fit = args.payload / args.ram_variant / 'image.itb'
selected = fit_images(fit)['kernel']
members, start, end = embedded_rootfs(selected)
runtime = []
if args.runtime:
    for name in ['wireless', 'network', 'firewall', 'dhcp', 'pon']:
        source = args.runtime / name
        assert members['etc/config/' + name]['data'] == source.read_bytes(), name
        runtime.append({'path': 'etc/config/' + name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    for source in (args.runtime / 'dropbear').iterdir():
        assert members['etc/dropbear/' + source.name]['data'] == source.read_bytes(), source.name
        runtime.append({'path': 'etc/dropbear/' + source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
functions = []
pe_firmware = []
linked = args.payload / 'vmlinux-initramfs.debug'
if not linked.exists(): linked = kernel.parent / 'vmlinux-initramfs.debug'
with linked.open('rb') as stream:
    elf = ELFFile(stream)
    table = elf.get_section_by_name('.symtab')
    base = table.get_symbol_by_name('_text')[0]['st_value']
    for name in ['wifi_ingress', 'wifi_receive', 'rtl9617c_wifi_setup_tc', 'rtl9617c_ni_wifi_attach',
                 'rtl9617c_ni_wifi_detach', 'rtl_qm_rx_start', 'rtl_qm_rx_poll', 'rtl_dma_tx_wifi', 'fe_prepare',
                 'fe_parse', 'fe_replace', 'rtl_fe_flush_port', 'rtl_ax_net_setup_tc',
                 'rtl_ax_set_key', 'rtl_ax_tx_submit', 'rtl_ax_tx_flush_key',
                 'fe_block_release', 'fe_cleanup_work', 'rtl_ax_cleanup_work',
                 'rtl_ax_station_remove', 'rtl_ax_ampdu_action', 'rtl_ax_tx_wait_station',
                 'rtl_ax_tx_queues', 'rtl_ax_wake_tx_queue', 'rtl_ax_tx_pause',
                 'rtl_ax_tx_open', 'rtl_ax_remove', 'rtl_ax_shutdown',
                 'ieee80211_subif_start_xmit_8023', 'rtl_engine_start',
                 'rtl9617c_wifi_id', 'rtl_ax_sta_state', 'rtl_ax_offload_work',
                 'rtl_ax_get_key_seq', 'rtl_ax_tx_ni_peer', 'rtl_ax_tx_ni_retire_peer',
                 'rtl_ax_rx_pe_rpq_start', 'rtl_ax_rx_pe_rpq_stop',
                 'rtl_ax_rx_uplink_retire', 'rtl_ax_rx_uplink_enable',
                 'rtl_ax_rx_uplink_disable', 'rtl_ax_tx_peer_authorized', 'ni_restore_registers']:
        symbols = table.get_symbol_by_name(name) or [s for s in table.iter_symbols() if s.name.startswith(name + '.')]
        assert symbols, name
        for symbol in symbols:
            section = elf.get_section(symbol['st_shndx'])
            offset = symbol['st_value'] - section['sh_addr']
            code = section.data()[offset:offset + symbol['st_size']]
            at = symbol['st_value'] - base
            assert code and selected[at:at + len(code)] == code, symbol.name
            functions.append({'name': symbol.name, 'kernel_offset': at, 'bytes': len(code),
                              'sha256': hashlib.sha256(code).hexdigest()})
    for symbol in table.get_symbol_by_name('pe_tx_program') or []:
        section = elf.get_section(symbol['st_shndx'])
        offset = symbol['st_value'] - section['sh_addr']
        code = section.data()[offset:offset + symbol['st_size']]
        at = symbol['st_value'] - base
        assert selected[at:at + len(code)] == code, symbol.name
        assert code == (root / 'artifacts/pe-radio-production/pe-tx.bin').read_bytes()
        pe_firmware.append({'name': symbol.name, 'bytes': len(code), 'kernel_offset': at,
                            'sha256': hashlib.sha256(code).hexdigest()})
assert pe_firmware, 'linked PE firmware missing'
record = {'FIT_sha256': hashlib.sha256(fit.read_bytes()).hexdigest(),
          'private_runtime_files_exactly_preserved': runtime, 'native_code_verified': functions,
          'PE_firmware_exactly_preserved': pe_firmware,
          'kernel_bytes': len(selected), 'initramfs_packed_bytes': end - start,
          'linked_ELF_sha256': hashlib.sha256(linked.read_bytes()).hexdigest(),
          'initramfs_members': len(members), 'booted': False}
suffix = '' if args.ram_variant == 'ram-pcie' else '-' + args.ram_variant
(args.payload / ('wifi-ne-code-runtime' + suffix + '-verification.json')).write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'FIT_sha256': record['FIT_sha256'], 'kernel_bytes': len(selected),
                  'runtime_files': len(runtime), 'native_functions': len(functions),
                  'PE_firmware_bytes': sum(item['bytes'] for item in pe_firmware),
                  'initramfs_members': len(members)}, indent=2))
