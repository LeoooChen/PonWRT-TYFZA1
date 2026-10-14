#!/usr/bin/env python3
"""Prepare common SerDes firmware without embedding per-device information."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

project = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path, help='OpenWrt files/ or unpacked rootfs directory')
parser.add_argument('--stock', type=Path, default=project / 'analysis/private/live-files')
parser.add_argument('--manifest', type=Path, default=project / 'artifacts/pon-runtime-manifest.json')
parser.add_argument('--check-inputs-only', action='store_true')
args = parser.parse_args()
required_profiles = ('epon', 'epon_d10g', 'epon_bi10g', 'gpon', 'xgpon', 'xgspon')

try:
    # Check inputs before installing anything into the build tree. Previous
    # files/ contents never satisfy a missing original board input.
    inputs = [args.stock / f'etc/serdes/pon_serdes_{mode}.txt' for mode in required_profiles]
    for source in inputs:
        if not source.is_file():
            raise ValueError(f'Missing mandatory PON input: {source}')
    if args.check_inputs_only:
        print('Mandatory PON board inputs are present')
        raise SystemExit(0)
    project.joinpath('artifacts').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pon-runtime-', dir=project / 'artifacts') as temporary:
        stage = Path(temporary)
        subprocess.run([sys.executable, str(project / 'tools/prepare_pon_serdes.py'),
                        str(args.stock / 'etc/serdes'), str(stage / 'firmware')], check=True)
        firmware = args.output / 'lib/firmware/realtek/rtl9617c'
        firmware.mkdir(parents=True, exist_ok=True)
        (firmware / 'bosa-calibration.bin').unlink(missing_ok=True)
        for source in (stage / 'firmware').glob('*.bin'):
            shutil.copyfile(source, firmware / source.name)
            (firmware / source.name).chmod(0o644)
        target_config = args.output / 'etc/config/pon'
        target_config.parent.mkdir(parents=True, exist_ok=True)
        target_config.touch(mode=0o600, exist_ok=True)
        target_config.chmod(0o600)
        shutil.copyfile(project / 'port/overlay/target/linux/rtl9617c/base-files/etc/config/pon', target_config)
        manifest = {
            'profiles': json.loads((stage / 'firmware/manifest.json').read_text()),
            'per_device_source': 'ubi0:ubi_Config, read on the target before kmodloader',
            'embedded_subscriber_identity': False,
            'required_modes': required_profiles,
            'output': str(args.output.resolve()),
        }
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2) + '\n')
except (ValueError, OSError, subprocess.CalledProcessError) as error:
    parser.exit(1, f'PON runtime preparation failed: {error}\n')
print('PON runtime ready: six SerDes profiles and empty public PON configuration')
