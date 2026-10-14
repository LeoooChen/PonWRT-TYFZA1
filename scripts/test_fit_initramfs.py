#!/usr/bin/env python3
"""Verify gzip/uncompressed kernel rootfs extraction and reject damaged archives."""
import gzip
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from fit_payload import embedded_rootfs

def archive(files):
    data = bytearray()
    for index, (name, content) in enumerate(list(files.items()) + [('TRAILER!!!', b'')]):
        fields = [index, 0o100644, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name) + 1, 0]
        data += b'070701' + ''.join(f'{x:08x}' for x in fields).encode() + name.encode() + b'\0'
        data += bytes(-len(data) % 4)
        data += content
        data += bytes(-len(data) % 4)
    return bytes(data)

inputs = {'etc/config/pon': b'config pon\n', 'sbin/procd': b'ELF-test'}
cpio = archive(inputs)
for packed in (cpio, gzip.compress(cpio, mtime=0)):
    prefix = b'ARM64-kernel' + gzip.compress(b'incidental data', mtime=0) + b'\0' * 17
    image = prefix + packed + b'\0' * 32
    members, start, end = embedded_rootfs(image)
    assert start == len(prefix) and end == len(prefix) + len(packed)
    assert all(members[name]['data'] == content for name, content in inputs.items())
for image in (gzip.compress(cpio)[:-4], cpio[:-20], cpio + gzip.compress(cpio)):
    try:
        embedded_rootfs(image)
    except ValueError:
        pass
    else:
        raise AssertionError('Truncated or duplicate actual rootfs accepted')
print('FIT initramfs: uncompressed/gzip actual contents and offsets; truncated/duplicate archives rejected')
