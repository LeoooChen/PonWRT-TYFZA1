#!/usr/bin/env python3
"""Inspect native build artifacts offline. This is NOT a boot/flash/PON test."""
import argparse
import hashlib
import json
import struct
import subprocess
from pathlib import Path

from verify_dtb import inspect as inspect_dtb

PIN = 'c759267c92b0697a6fd6f369164a5ed4c1a6a03c'


def one(directory, pattern):
    found = sorted(directory.glob(pattern))
    if len(found) != 1:
        raise ValueError(f'Expected one {pattern}, found {len(found)}')
    return found[0]


def image_info(path):
    data = path.read_bytes()
    if len(data) < 64 or data[56:60] != b'ARM\x64':
        raise ValueError(f'Not an uncompressed ARM64 Image: {path}')
    offset, size, flags = struct.unpack_from('<QQQ', data, 8)
    if size == 0 or flags & 1 or not flags & 8:
        raise ValueError(f'Unsupported ARM64 image placement/endianness: {path}')
    if b'Linux version 6.18.52 ' not in data:
        raise ValueError(f'Wrong kernel version: {path}')
    return {'bytes': len(data), 'image_size': size, 'text_offset': offset,
            'flags': flags, 'sha256': hashlib.sha256(data).hexdigest()}


def verify(tree, stock):
    commit = subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != PIN:
        raise ValueError('Wrong OpenWrt revision')
    directory = tree / 'bin/targets/rtl9617c/generic'
    initramfs = one(directory, '*-initramfs-Image')
    kernel = one(directory, '*tyfza1-Image')
    rootfs = one(directory, '*-rootfs.squashfs')
    images = {p.name: image_info(p) for p in (kernel, initramfs)}
    if initramfs.read_bytes() == kernel.read_bytes():
        raise ValueError('initramfs and ordinary kernel are identical')
    dtbs = {}
    for suffix in ('board.dtb', 'nand-readonly.dtb'):
        dtb = one(directory, '*-' + suffix)
        dtbs[dtb.name] = inspect_dtb(stock, dtb)
    unsquashfs = tree / 'staging_dir/host/bin/unsquashfs4'
    listing = subprocess.check_output([str(unsquashfs), '-ll', str(rootfs)], text=True)
    for forbidden in ('ca-ne.ko', 'ca-rtk.ko', '/omci_app', '/eponoamd', '/5.10.', 'airoha-xpon.ko'):
        if forbidden in listing:
            raise ValueError(f'Unexpected vendor runtime in rootfs: {forbidden}')
    elfs = {}
    for member in ('bin/busybox', 'sbin/procd'):
        data = subprocess.check_output([str(unsquashfs), '-cat', str(rootfs), member])
        if data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
            raise ValueError(f'Rootfs executable is not AArch64 LE: {member}')
        elfs[member] = hashlib.sha256(data).hexdigest()
    upgrade = subprocess.check_output([str(unsquashfs), '-cat', str(rootfs),
                                       'lib/upgrade/platform.sh'], text=True)
    if 'no flash operation performed' not in upgrade or 'return 1' not in upgrade:
        raise ValueError('Missing persistent upgrade guard')
    manifest = one(directory, '*.manifest').read_text()
    for required in ('base-files', 'busybox', 'procd', 'ubi-utils', 'nand-utils'):
        if not any(line.startswith(required + ' ') for line in manifest.splitlines()):
            raise ValueError(f'Missing required package: {required}')
    kdir = tree / 'build_dir/target-aarch64_cortex-a55_musl/linux-rtl9617c_generic'
    config = (kdir / 'linux-6.18.52/.config').read_text().splitlines()
    for symbol in ('ARM64', 'ARM_GIC_V3', 'ARM_ARCH_TIMER', 'ARM_PSCI_FW',
                   'SERIAL_RTL9617C', 'SERIAL_RTL9617C_CONSOLE', 'SPI_RTL9617C',
                   'MTD_SPI_NAND', 'MTD_UBI', 'MTD_UBI_BLOCK', 'SQUASHFS',
                   'RTL9617C_WATCHDOG', 'RTL9617C_PER_IRQ', 'MDIO_RTL9617C',
                   'WATCHDOG_HANDLE_BOOT_ENABLED', 'ARM64_ERRATUM_1024718',
                   'ARM64_ERRATUM_1530923', 'BLK_DEV_DM', 'DM_INIT', 'DM_VERITY',
                   'CRYPTO_SHA256'):
        if f'CONFIG_{symbol}=y' not in config:
            raise ValueError(f'Missing built-in kernel support: {symbol}')
    if 'CONFIG_WATCHDOG_OPEN_TIMEOUT=60' not in config:
        raise ValueError('Trial-slot watchdog handoff must not feed forever before procd opens it')
    return {'scope': 'offline artifact inspection only', 'openwrt_commit': commit,
            'kernel_version': '6.18.52', 'images': images, 'dtbs': dtbs,
            'rootfs_sha256': hashlib.sha256(rootfs.read_bytes()).hexdigest(),
            'rootfs_aarch64_executables': elfs, 'upgrade_guard': 'rejects writes',
            'hardware_boot_tested': False, 'native_ubi_installation_ready': False,
            'lan_tested': False, 'pon_service_tested': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tree', type=Path)
    parser.add_argument('stock_dtb', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        result = json.dumps(verify(args.tree.resolve(), args.stock_dtb), indent=2) + '\n'
        if args.report:
            with args.report.open('x') as output:
                output.write(result)
        print(result, end='')
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Artifact verification failed: {error}\n')
