#!/usr/bin/env python3
"""Convert the TYFZA1's original P6 profiles; prefer its revision-C table."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def convert(source):
    rows = []
    for line in source.read_text().splitlines():
        if not line.strip().startswith('0x'):
            continue
        address, value, _, delay = (int(word, 0) for word in line.split())
        offset = address - 0xf550d000
        if offset < 0 or offset > 0x5c or offset & 3:
            raise ValueError(f'P6 address outside original QX resource: {address:#x}')
        rows.append((offset, value, delay or 10))
    if not rows or len(rows) > 512:
        raise ValueError(f'P6 profile exceeds the original loader: {source.name}')
    return struct.pack('<4sII', b'TP6S', 1, len(rows)) + b''.join(
        struct.pack('<III', *row) for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for mode in ('sgmii_2g5', 'usxgmii_10g'):
        stem = 'p6_serdes_' + mode
        source = args.original / (stem + '_C.txt')
        if not source.exists():
            source = args.original / (stem + '.txt')
        data = convert(source)
        output = args.output / (stem + '.bin')
        output.write_bytes(data)
        manifest[stem] = {'source': source.name,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'firmware_sha256': hashlib.sha256(data).hexdigest(),
            'rows': (len(data) - 12) // 12}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
