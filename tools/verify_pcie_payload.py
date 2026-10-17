#!/usr/bin/env python3
"""Verify the selected PCIe FIT kernel/DTB and its stock reset wiring."""
import argparse
import hashlib
import json
from pathlib import Path
from fit_payload import fit_images
from verify_pcie_dtb import verify

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('payload', type=Path)
args = parser.parse_args()
fit_path = args.payload / 'ram-pcie/image.itb'
manifest = json.loads((args.payload / 'ram-pcie/manifest.json').read_text())
assert hashlib.sha256(fit_path.read_bytes()).hexdigest() == manifest['fit_sha256']
stock = (root / 'evidence/wireless-study-20261008/evidence/stock/live-fit.dtb').read_bytes()
boards = [('conf-1', '', 'dtb_sha256'), ('conf-0', '-2p5g', 'dtb_2p5g_sha256')] if 'dtb_2p5g_sha256' in manifest else [(None, '', 'dtb_sha256')]
checked = []
for config, suffix, digest in boards:
    actual = fit_images(fit_path, config)
    assert actual['fdt'] == (args.payload / f'firmware/openwrt-rtl9617c-generic-tianyi_tyfza1-pcie{suffix}.dtb').read_bytes()
    assert hashlib.sha256(actual['fdt']).hexdigest() == manifest[digest]
    assert actual['kernel'] == next((args.payload / 'firmware').glob('*initramfs-Image')).read_bytes()
    checked.append({'configuration': config, 'reset_and_resources': verify(stock, actual['fdt'])})
report = {'FIT_sha256': manifest['fit_sha256'], 'selected_kernel_and_DTB_match_firmware': True,
          'boards': checked, 'hardware_verified': False}
(args.payload / 'pcie-payload-verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
