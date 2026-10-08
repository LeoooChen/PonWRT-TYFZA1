#!/usr/bin/env python3
"""Prepare mandatory board runtime files for every TYFZA1 build entry point."""
import argparse
import hashlib
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
parser.add_argument('--auth-config', type=Path, default=project / 'analysis/private/pon-auth.json')
parser.add_argument('--manifest', type=Path, default=project / 'artifacts/pon-runtime-manifest.json')
args = parser.parse_args()
required_profiles = ('epon', 'epon_d10g', 'epon_bi10g', 'gpon', 'xgpon', 'xgspon')

try:
    # Check inputs before installing anything into the build tree. Previous
    # files/ contents never satisfy a missing original board input.
    inputs = [args.stock / f'etc/serdes/pon_serdes_{mode}.txt' for mode in required_profiles]
    calibration = args.stock / 'var/config/rtkbosa_k.bin'
    config = args.stock / 'var/config/config.xml'
    scfg = args.stock / 'var/config/default_scfg.txt'
    for source in inputs + [calibration, config, scfg, args.auth_config]:
        if not source.is_file():
            raise ValueError(f'Missing mandatory PON input: {source}')
    if calibration.stat().st_size != 1024:
        raise ValueError('This board requires the original 1024-byte BOSA calibration')
    project.joinpath('artifacts').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pon-runtime-', dir=project / 'artifacts') as temporary:
        stage = Path(temporary)
        subprocess.run([sys.executable, str(project / 'tools/prepare_pon_serdes.py'),
                        str(args.stock / 'etc/serdes'), str(stage / 'firmware')], check=True)
        subprocess.run([sys.executable, str(project / 'tools/prepare_pon_identity.py'),
                        str(config), str(scfg), str(stage / 'pon'),
                        '--auth-config', str(args.auth_config)], check=True)
        shutil.copyfile(calibration, stage / 'firmware/bosa-calibration.bin')
        firmware = args.output / 'lib/firmware/realtek/rtl9617c'
        firmware.mkdir(parents=True, exist_ok=True)
        for source in (stage / 'firmware').glob('*.bin'):
            shutil.copyfile(source, firmware / source.name)
            (firmware / source.name).chmod(0o644)
        target_config = args.output / 'etc/config/pon'
        target_config.parent.mkdir(parents=True, exist_ok=True)
        # Establish the private mode before writing LOID into the destination.
        target_config.touch(mode=0o600, exist_ok=True)
        target_config.chmod(0o600)
        shutil.copyfile(stage / 'pon', target_config)
        manifest = {
            'profiles': json.loads((stage / 'firmware/manifest.json').read_text()),
            'calibration_sha256': hashlib.sha256(calibration.read_bytes()).hexdigest(),
            'identity': json.loads((stage / 'pon.receipt.json').read_text()),
            'required_modes': required_profiles,
            'output': str(args.output.resolve()),
        }
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2) + '\n')
except (ValueError, OSError, subprocess.CalledProcessError) as error:
    parser.exit(1, f'PON runtime preparation failed: {error}\n')
print('PON runtime ready: SerDes profiles, original BOSA calibration and private ONU configuration')
