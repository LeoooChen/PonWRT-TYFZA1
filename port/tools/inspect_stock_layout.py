#!/usr/bin/env python3
"""Decode verified TYFZA1 environment/BBL evidence. Never execute boot commands."""
import argparse
import binascii
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

DUMP_SHA256 = 'e6c8920b27922491faa7d0ac162ea2419bf6aadaef616ece33bce36436b778d6'
BOOT_KEYS = {'b', 'b1', 'b2', 'basicargs', 'bootcmd', 'boot_by_commit',
             'boot_by_tryactive', 'set_act0', 'set_act1', 'en_wdt', 'process1',
             'process2', 'setbootargs', 'setmoreargs', 'ramargs', 'mtdparts',
             'mtdids', 'ubi_mtd_num', 'ubi_device_name', 'unzip_src',
             'fdt_addr_r', 'kernel_addr_r', 'sw_active', 'sw_commit', 'sw_tryactive'}


def parse_env(data):
    if len(data) < 7 or zlib.crc32(data[5:]) != struct.unpack_from('<I', data)[0]:
        raise ValueError('Environment CRC mismatch')
    payload, end, _ = data[5:].partition(b'\0\0')
    if not end:
        raise ValueError('Unterminated environment')
    result = {}
    for item in payload.split(b'\0'):
        key, sep, value = item.partition(b'=')
        if not sep or not re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError('Invalid environment entry')
        name = key.decode('ascii')
        if name in result:
            raise ValueError('Duplicate environment entry')
        result[name] = value.decode('ascii')
    return data[4], result


def parse_bbl(header, table, erase_size, flash_size):
    if len(header) != 16 or len(table) != 512:
        raise ValueError('Truncated BBL')
    magic, count, crc0, crc1 = struct.unpack('<4I', header)
    if magic not in (0x42626c30, 0x316c6242) or count > 128:
        raise ValueError('Invalid BBL header')
    if crc0 != crc1 or crc0 != binascii.crc_hqx(table, 0):
        raise ValueError('BBL CRC mismatch')
    bad = list(struct.unpack('<128I', table)[:count])
    if bad != sorted(set(bad)) or any(x % erase_size or x >= flash_size for x in bad):
        raise ValueError('Invalid BBL block offsets')
    return {'magic': hex(magic), 'count': count, 'crc16': hex(crc0), 'bad_blocks': bad}


def logical_to_physical(offset, bad_blocks, erase_size, flash_size):
    if offset < 0 or offset >= flash_size - len(bad_blocks) * erase_size:
        raise ValueError('Logical offset outside visible NAND')
    for bad in bad_blocks:
        if bad <= offset:
            offset += erase_size
        else:
            break
    if offset >= flash_size:
        raise ValueError('Translation exceeds NAND')
    return offset


def parse_parts(value):
    if not value.startswith('mtdparts=ca_spinand_flash:'):
        raise ValueError('Not the TYFZA1 master flash definition')
    position = 0
    parts = []
    for part in value.split(':', 1)[1].split(','):
        match = re.fullmatch(r'(\d+)([KM]?)(?:@(0x[0-9a-fA-F]+))?\(([A-Za-z0-9_]+)\)', part)
        if not match:
            raise ValueError('Unsupported partition syntax')
        size, unit, offset, name = match.groups()
        size = int(size) * {'': 1, 'K': 1024, 'M': 1024**2}[unit]
        start = int(offset, 0) if offset else position
        if start < position or size <= 0:
            raise ValueError('Overlapping or empty partition')
        parts.append({'name': name, 'offset': start, 'size': size})
        position = start + size
    return parts


def inspect(path):
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if digest != DUMP_SHA256:
        raise ValueError('Not the identified TYFZA1 data-only backup; do not apply this layout to another unit')
    environments, tables = [], []
    with path.open('rb') as source:
        for offset in (0x280000, 0x2c0000):
            source.seek(offset)
            flags, values = parse_env(source.read(0x40000))
            environments.append({'offset': offset, 'flags': flags,
                                 'boot_variables': {k: v for k, v in values.items() if k in BOOT_KEYS}})
        for offset in (0x200000, 0x240000):
            source.seek(offset)
            header = source.read(16)
            source.seek(offset + 2048)
            tables.append({'offset': offset, **parse_bbl(header, source.read(512), 131072, path.stat().st_size)})
    if environments[0]['boot_variables'] != environments[1]['boot_variables']:
        raise ValueError('Redundant boot environments disagree')
    if tables[0]['bad_blocks'] != tables[1]['bad_blocks']:
        raise ValueError('Redundant factory BBLs disagree')
    parts = parse_parts(environments[0]['boot_variables']['mtdparts'])
    if max(p['offset'] + p['size'] for p in parts) > path.stat().st_size:
        raise ValueError('Partitions exceed NAND')
    return {'scope': 'identified community backup only; no live board validation',
            'dump_sha256': digest, 'environments': environments, 'factory_bbl': tables,
            'partitions': parts, 'factory_translation_is_identity': not tables[0]['bad_blocks'],
            'stock_root_backend': 'MTD_UBI_GLUEBI + MTD_BLOCK, not ubiblock',
            'persistent_installation_approved': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    output = json.dumps(inspect(args.dump), indent=2) + '\n'
    if args.report:
        with args.report.open('x', encoding='utf-8') as dest:
            dest.write(output)
    print(output, end='')
