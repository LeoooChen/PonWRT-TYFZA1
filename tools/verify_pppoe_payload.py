#!/usr/bin/env python3
"""Inspect PPPoE runtime and native forwarding code in the final RAM image."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

from fit_payload import fit_images, embedded_rootfs

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('payload', type=Path)
args = parser.parse_args()
kernel = Path(subprocess.check_output([
    'python3', str(root / 'port/tools/project_paths.py'), '--kernel-directory',
    str(root / 'sources/openwrt')], text=True).strip())
raw = fit_images(args.payload / 'ram-pcie/image.itb')['kernel']
members, _, _ = embedded_rootfs(raw)
required = [
    'usr/sbin/pppd', 'usr/lib/pppd/2.5.3/pppoe.so', 'lib/netifd/proto/ppp.sh',
    'lib/modules/6.18.55/ppp_generic.ko', 'lib/modules/6.18.55/pppox.ko',
    'lib/modules/6.18.55/pppoe.ko', 'lib/modules/6.18.55/nf_flow_table.ko',
]
for name in required:
    assert name in members, name
packed = ELFFile(BytesIO(members[required[-1]]['data']))
with (kernel / 'net/netfilter/nf_flow_table.ko').open('rb') as stream:
    built = ELFFile(stream)
    assert packed.get_section_by_name('.text').data() == built.get_section_by_name('.text').data()
functions = []
with (args.payload / 'vmlinux-initramfs.debug').open('rb') as stream:
    elf = ELFFile(stream)
    symbols = elf.get_section_by_name('.symtab')
    base = symbols.get_symbol_by_name('_text')[0]['st_value']
    for name in ('fe_prepare', 'fe_parse', 'fe_replace', 'fe_delete',
                 'fe_block_callback', 'fe_counters_show'):
        found = symbols.get_symbol_by_name(name) or [
            item for item in symbols.iter_symbols() if item.name.startswith(name + '.')]
        assert found, name
        for item in found:
            section = elf.get_section(item['st_shndx'])
            offset = item['st_value'] - section['sh_addr']
            code = section.data()[offset:offset + item['st_size']]
            offset = item['st_value'] - base
            assert code == raw[offset:offset + len(code)], name
            functions.append(item.name)
js = members['www/luci-static/resources/view/network/tyfza1-pon.js']['data']
for name in (b'wan_device', b'wan_username', b'wan_password', b"'@wan'"):
    assert name in js, name
report = {
    'candidate': str(args.payload), 'booted': False,
    'ppp_runtime_members': required, 'packed_flowtable_code_matches_build': True,
    'native_FE_code_matches_final_FIT': functions,
    'native_WAN_configuration_included': True, 'hardware_PPPOE_line_verified': False,
}
(args.payload / 'pppoe-payload-verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
