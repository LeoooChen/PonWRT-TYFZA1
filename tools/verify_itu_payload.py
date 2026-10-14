#!/usr/bin/env python3
"""Confirm native ITU control code and its sysfs ABI in the final RAM image."""
import argparse
import json
from pathlib import Path
import sys

from fit_payload import fit_images, embedded_rootfs

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('payload', type=Path)
args = parser.parse_args()
raw = fit_images(args.payload / 'ram-pcie/image.itb')['kernel']
members, _, _ = embedded_rootfs(raw)
functions = []
with (args.payload / 'vmlinux-initramfs.debug').open('rb') as stream:
    elf = ELFFile(stream)
    symbols = elf.get_section_by_name('.symtab')
    base = symbols.get_symbol_by_name('_text')[0]['st_value']
    for name in ('rtl_pon_itu_state', 'rtl_pon_ploam_receive', 'rtl_pon_ploam_transmit',
                 'ploam_read', 'ploam_write', 'ploam_default_write',
                 'rtl_pon_gpon_prepare_locked', 'rtl_pon_gpon_identity', 'gpon_identity_write',
                 'rtl_pon_gpon_activation_locked', 'rtl_pon_gpon_irq',
                 'rtl_pon_xgpon_prepare_locked', 'rtl_pon_xgpon_identity', 'xgpon_identity_write',
                 'rtl_pon_puc_configure_locked', 'rtl_omci_transmit', 'rtl_pon_omci_receive',
                 'rtl_dma_tx_frame'):
        found = symbols.get_symbol_by_name(name) or [
            item for item in symbols.iter_symbols() if item.name.startswith(name + '.')]
        assert found, name
        for item in found:
            section = elf.get_section(item['st_shndx'])
            offset = item['st_value'] - section['sh_addr']
            code = section.data()[offset:offset + item['st_size']]
            offset = item['st_value'] - base
            assert code and code == raw[offset:offset + len(code)], item.name
            functions.append({'name': item.name, 'bytes': len(code)})
    for name in ('bin_attr_ploam', 'bin_attr_ploam_default', 'bin_attr_gpon_identity',
                 'bin_attr_xgpon_identity', 'dev_attr_activation'):
        assert symbols.get_symbol_by_name(name), name
js = members['www/luci-static/resources/view/network/tyfza1-pon.js']['data']
assert b"'activation'" in js
assert 'usr/sbin/pon-identity' in members
assert b'pon-identity' in members['etc/init.d/pon']['data']
assert b'xgpon_identity' in members['etc/init.d/pon']['data']
assert b'pon-omci up' in members['etc/init.d/pon']['data']
report = {'candidate': str(args.payload), 'native_ITU_functions': functions,
          'PLOAM_sysfs_attributes_present': True, 'protocol_MAC_status_in_UI': True,
          'native_GPON_cold_preparation_and_UCI_identity_loader_present': True,
          'GPON_hardware_activation_IRQ_and_raw_OMCI_transport_present': True,
          'XG_PON_and_XGS_PON_cold_MAC_preparation_and_36byte_identity_present': True,
          'ITU_PUC_independent_of_EPON_present': True,
          'OMCI_business_backend_complete': False, 'XGTC_activation_complete': False,
          'booted': False, 'PON_activation_and_business_complete': False}
(args.payload / 'itu-payload-verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
