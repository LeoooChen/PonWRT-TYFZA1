#!/usr/bin/env python3
"""Check vendor environment and factory-BBL decoding without using hardware."""
import binascii
import struct
import unittest
import zlib
from inspect_stock_layout import parse_env, parse_bbl, logical_to_physical, parse_parts


def environment(payload):
    payload += b'\0\0' + b'\xff' * 64
    return struct.pack('<I', zlib.crc32(payload)) + b'\x39' + payload


def bbl(offsets):
    table = struct.pack('<128I', *(offsets + [0xffffffff] * (128 - len(offsets))))
    crc = binascii.crc_hqx(table, 0)
    return struct.pack('<4I', 0x42626c30, len(offsets), crc, crc), table


class StockLayout(unittest.TestCase):
    def test_environment_crc(self):
        flags, values = parse_env(environment(b'b=source ${unzip_src}:script\0sw_active=0'))
        self.assertEqual(flags, 57)
        self.assertEqual(values['b'], 'source ${unzip_src}:script')

    def test_reject_corrupt_environment(self):
        data = bytearray(environment(b'x=1')); data[-1] ^= 1
        with self.assertRaisesRegex(ValueError, 'CRC'):
            parse_env(data)

    def test_reject_duplicate_environment(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            parse_env(environment(b'x=1\0x=2'))

    def test_empty_bbl(self):
        header, table = bbl([])
        self.assertEqual(parse_bbl(header, table, 0x20000, 0x10000000)['count'], 0)
        self.assertEqual(binascii.crc_hqx(table, 0), 0x7fa1)

    def test_reject_bbl_checksum(self):
        header, table = bbl([])
        with self.assertRaisesRegex(ValueError, 'CRC'):
            parse_bbl(header, b'\x00' + table[1:], 0x20000, 0x10000000)

    def test_reject_unordered_bbl(self):
        with self.assertRaisesRegex(ValueError, 'offsets'):
            parse_bbl(*bbl([0x40000, 0x20000]), 0x20000, 0x10000000)

    def test_reject_unaligned_bbl(self):
        with self.assertRaisesRegex(ValueError, 'offsets'):
            parse_bbl(*bbl([1]), 0x20000, 0x10000000)

    def test_vendor_skip_consecutive_bad_blocks(self):
        self.assertEqual(logical_to_physical(0x20010, [0x20000, 0x40000],
                                           0x20000, 0x100000), 0x60010)

    def test_translation_leaves_earlier_offsets(self):
        self.assertEqual(logical_to_physical(0x10, [0x20000], 0x20000, 0x100000), 0x10)

    def test_translation_out_of_bounds(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            logical_to_physical(0xe0000, [0x20000], 0x20000, 0x100000)

    def test_partition_gap_preserved(self):
        parts = parse_parts('mtdparts=ca_spinand_flash:2048K(boot),256K@0x280000(env)')
        self.assertEqual(parts[1]['offset'], 0x280000)

    def test_partition_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            parse_parts('mtdparts=ca_spinand_flash:2048K(boot),256K@0x100000(env)')


if __name__ == '__main__':
    unittest.main()
