#!/usr/bin/env python3
"""Install common vendor firmware only; per-unit data is read on the target."""
import argparse
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('inputs', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
firmware = args.inputs / 'lib/firmware/realtek'
for source in firmware.rglob('*'):
    if not source.is_file():
        continue
    if source.name in ('bosa-calibration.bin', 'flash-calibration.bin'):
        raise ValueError('Runtime input bundle must not contain per-device calibration')
    destination = args.output / 'lib/firmware/realtek' / source.relative_to(firmware)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o644)
for mode in ('epon', 'epon_d10g', 'epon_bi10g', 'gpon', 'xgpon', 'xgspon'):
    if not (args.output / f'lib/firmware/realtek/rtl9617c/pon_serdes_{mode}.bin').is_file():
        raise ValueError(f'Missing common SerDes firmware: {mode}')
config = args.output / 'etc/config/pon'
config.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(root / 'port/overlay/target/linux/rtl9617c/base-files/etc/config/pon', config)
config.chmod(0o600)
print('Installed common runtime inputs; subscriber configuration remains empty')
