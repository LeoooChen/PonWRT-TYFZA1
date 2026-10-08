#!/usr/bin/env python3
"""Current source lock and identified TYFZA1 evidence locations."""
import argparse
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
PORT = PROJECT / 'port'
EVIDENCE = PROJECT / 'evidence/rtl-research'


def source_lock():
    return json.loads((PORT / 'sources.lock.json').read_text())


def receipt(name):
    return EVIDENCE / 'data/receipts' / name


def kernel_directory(tree):
    version = source_lock()['openwrt']['kernel']
    found = list(Path(tree).glob(f'build_dir/target-*/linux-rtl9617c_generic/linux-{version}'))
    if len(found) != 1:
        raise ValueError(f'Expected one prepared Linux {version} tree, found {len(found)}')
    return found[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--field', choices=('kernel', 'openwrt-commit', 'pon-userspace-commit', 'uboot-version', 'uboot-sha256'))
    parser.add_argument('--kernel-directory', type=Path)
    args = parser.parse_args()
    if args.kernel_directory:
        print(kernel_directory(args.kernel_directory))
    else:
        section, key = {'kernel': ('openwrt', 'kernel'), 'openwrt-commit': ('openwrt', 'commit'),
                        'pon-userspace-commit': ('pon_userspace', 'commit'),
                        'uboot-version': ('uboot_tools', 'version'), 'uboot-sha256': ('uboot_tools', 'sha256')}[args.field]
        print(source_lock()[section][key])
