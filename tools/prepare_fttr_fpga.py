#!/usr/bin/env python3
"""Package the retained TYFZA1 PG2L100H image without changing its SPI bytes."""
import argparse
import hashlib
import json
from pathlib import Path

SIZE = 3789476
SHA256 = '9c7e8ba3b1521277d2af4b777869287010bc3e8e7884a2542f6ab5ec4af58ab5'


def prepare(source, output):
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != SIZE or digest != SHA256:
        raise ValueError('FPGA image does not match the retained TYFZA1 sample')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'fttr-top.bin').write_bytes(data)
    report = {'bytes': len(data), 'sha256': digest, 'output': 'fttr-top.bin',
              'scope': 'Retained image identity and byte preservation; no hardware loading or compatibility claim for other samples.'}
    (output / 'fpga-manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.original, args.output), indent=2))


if __name__ == '__main__':
    main()
