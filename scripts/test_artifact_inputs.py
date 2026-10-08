#!/usr/bin/env python3
"""Exercise final-FIT binding and actual libuci string/input validation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from uci_config import field, quote, validate
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--payload', type=Path, default=root / 'artifacts/full-pon-control-transactions-6.18.55')
parser.add_argument('--old-payload', type=Path, default=root / 'artifacts/full-pon-review-fixes-6.18.55')
args = parser.parse_args()
checks = []

with tempfile.TemporaryDirectory(prefix='artifact-input-test-', dir=root / 'artifacts') as temporary:
    temporary = Path(temporary)
    config = temporary / 'pon'
    config.touch(mode=0o600)
    values = ['abcde', "ab'cd", 'ab\\cd', 'a"bcd', "é'yz", '']
    for value in values:
        config.write_text("config pon 'pon'\n option password " + quote(value) + '\n')
        validate(config, {'password': value})
    checks.append('Actual OpenWrt libuci round-trips apostrophe, backslash, double quote, UTF-8 and empty strings')
    for value in ('ab\ncd', 'ab\rcd', 'ab\tcd', 'ab\0cd', 'ab\x7fcd', 'é' * 7):
        try:
            field(value, 'synthetic password', 12)
        except ValueError:
            pass
        else:
            raise AssertionError('Control character or excessive byte length accepted')
    assert field('é' * 6, 'synthetic password', 12)
    checks.append('Control characters rejected and UTF-8 limits measured in bytes')
    mixed = temporary / 'mixed'
    (mixed / 'ram-pon').mkdir(parents=True)
    (mixed / 'firmware').symlink_to(args.payload.resolve() / 'firmware', target_is_directory=True)
    for name in ('image.itb', 'manifest.json'):
        (mixed / 'ram-pon' / name).symlink_to(args.old_payload.resolve() / 'ram-pon' / name)
    result = subprocess.run([sys.executable, str(root / 'tools/verify_pon_payload.py'), str(mixed),
                             '--build-unit', 'mixed-negative', '--output', str(temporary / 'mixed.json')],
                            capture_output=True, text=True)
    assert result.returncode and 'Final FIT kernel does not match firmware directory' in result.stderr
    assert not (temporary / 'mixed.json').exists()
    checks.append('Actual final-FIT verifier rejects new firmware plus old FIT and valid old manifest')
    result = subprocess.run([sys.executable, str(root / 'tools/verify_pon_payload.py'), str(args.payload),
                             '--build-unit', 'input-test', '--output', str(temporary / 'valid.json')],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((temporary / 'valid.json').read_text())
    assert report['FIT_kernel_DTB_and_initramfs_match_external_files'] and report['initramfs']['members'] > 100
    checks.append('Positive final FIT yields actual kernel, DTB and complete rootfs comparison')
(root / 'logs/artifact-input-tests.json').write_text(json.dumps({'passed': checks}, indent=2) + '\n')
print(f'{len(checks)} final-FIT and libuci input checks passed')
