#!/usr/bin/env python3
"""Build the real-skb checksum test; run its module only on a matching test kernel."""
from pathlib import Path
import argparse
import os
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('kernel', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
source = (root / 'tools/iperf-pon/iperf_pon_feed.c').read_text()
start = source.index('static int copy_payload(')
end = source.index('\nstatic netdev_tx_t transmit', start)
(args.output / 'copy-payload.h').write_text(source[start:end])
(args.output / 'checksum_selftest.c').write_text((root / 'scripts/iperf_pon_checksum_test.c').read_text())
(args.output / 'Makefile').write_text('obj-m := checksum_selftest.o\n')
subprocess.run(['make', '-C', str(args.kernel.resolve()), 'ARCH=arm64',
                'CROSS_COMPILE=aarch64-openwrt-linux-musl-',
                'M=' + str(args.output.resolve()), 'modules'], check=True)
print('Read /sys/module/checksum_selftest/parameters/{result,cases,zero_cases,invalid_cases}')
print('Expected: 0, 84, 6, 3; unload checksum_selftest after verification.')
