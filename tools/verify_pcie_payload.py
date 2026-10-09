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
actual = fit_images(fit_path)
assert actual['fdt'] == (args.payload / 'firmware/openwrt-rtl9617c-generic-tianyi_tyfza1-pcie.dtb').read_bytes()
assert actual['kernel'] == next((args.payload / 'firmware').glob('*initramfs-Image')).read_bytes()
stock = (root / 'evidence/wireless-study-20261008/evidence/stock/live-fit.dtb').read_bytes()
report = {'FIT_sha256': manifest['fit_sha256'], 'selected_kernel_and_DTB_match_firmware': True,
          'reset_and_resources': verify(stock, actual['fdt']), 'hardware_verified': False}
(args.payload / 'pcie-payload-verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
