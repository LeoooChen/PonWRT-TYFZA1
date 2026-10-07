#!/usr/bin/env python3
"""Negative tests for the offline ARM64 artifact checker."""
import struct
import tempfile
import unittest
from pathlib import Path
from verify_artifacts import image_info


class Arm64Image(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / 'test-Image'
        self.data = bytearray(64) + b'Linux version 6.18.52 (test)'
        self.data[56:60] = b'ARM\x64'
        struct.pack_into('<QQQ', self.data, 8, 0, 0x200000, 0xa)

    def check_image(self):
        self.image.write_bytes(self.data)
        return image_info(self.image)

    def test_header(self):
        self.assertEqual(self.check_image()['image_size'], 0x200000)

    def test_bad_magic(self):
        self.data[56] = 0
        with self.assertRaisesRegex(ValueError, 'ARM64'):
            self.check_image()

    def test_wrong_kernel(self):
        self.data[64:] = b'Linux version 5.10.70 '
        with self.assertRaisesRegex(ValueError, 'kernel version'):
            self.check_image()

    def test_empty_image_size(self):
        struct.pack_into('<Q', self.data, 16, 0)
        with self.assertRaisesRegex(ValueError, 'placement'):
            self.check_image()

    def test_big_endian_image(self):
        struct.pack_into('<Q', self.data, 24, 0xb)
        with self.assertRaisesRegex(ValueError, 'endianness'):
            self.check_image()

    def test_legacy_low_placement_only(self):
        struct.pack_into('<Q', self.data, 24, 2)
        with self.assertRaisesRegex(ValueError, 'placement'):
            self.check_image()


if __name__ == '__main__':
    unittest.main()
