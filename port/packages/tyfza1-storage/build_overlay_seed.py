#!/usr/bin/env python3
"""Build an empty TYFZA1 UBIFS seed from a Zstd-enabled mkfs.ubifs 2.3.1."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import zlib

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('mkfs', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
version = subprocess.check_output([str(args.mkfs), '--version'], text=True).strip()
if version != 'mkfs.ubifs (mtd-utils) 2.3.1':
    raise ValueError('Use the project-pinned mtd-utils 2.3.1')
with tempfile.TemporaryDirectory(prefix='tyfza1-overlay-seed-') as temporary:
    root = Path(temporary) / 'empty'
    root.mkdir()
    image = Path(temporary) / 'seed.ubifs'
    subprocess.run([str(args.mkfs), '-U', '-m', '2048', '-e', '126976', '-c', '1966',
                    '-x', 'zstd', '-F', '-r', str(root), '-o', str(image)], check=True)
    data = image.read_bytes()
magic, crc, _, length, kind = struct.unpack_from('<IIQIB', data)
geometry = struct.unpack_from('<4I', data, 32)
compressor = struct.unpack_from('<H', data, 84)[0]
if (magic, length, kind) != (0x06101831, 4096, 6):
    raise ValueError('Not a UBIFS superblock')
if zlib.crc32(data[8:length]) ^ 0xffffffff != crc:
    raise ValueError('UBIFS superblock CRC mismatch')
if (geometry[0], geometry[1], geometry[3], compressor) != (2048, 126976, 1966, 3):
    raise ValueError('Wrong geometry, growth limit or compressor')
with args.output.open('wb') as output:
    output.write(gzip.compress(data, mtime=0))
report = dict(mkfs_version=version, mkfs_sha256=hashlib.sha256(args.mkfs.read_bytes()).hexdigest(),
              image_sha256=hashlib.sha256(data).hexdigest(), image_bytes=len(data),
              gzip_bytes=args.output.stat().st_size, min_io_size=geometry[0],
              leb_size=geometry[1], initial_lebs=geometry[2], max_lebs=geometry[3],
              default_compressor='zstd', scope='Empty filesystem, no device data or flashing')
args.output.with_suffix(args.output.suffix + '.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
