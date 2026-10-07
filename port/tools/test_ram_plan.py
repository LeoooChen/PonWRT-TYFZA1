#!/usr/bin/env python3
"""Host-only negative checks for RAM collisions; no emulated device or QEMU."""
import unittest
from plan_ram_boot import interval, validate


class RamPlacement(unittest.TestCase):
    def test_valid_disjoint_regions(self):
        validate([(0, 0x20000000)], [(0x18000000, 0x20000000)],
                 0x4000000, 0x800000, 0, 0x5000000, 4096)

    def test_reserved_memory_collision(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            validate([(0, 0x20000000)], [(0x4000000, 0x4100000)],
                     0x4000000, 0x800000, 0, 0x5000000, 4096)

    def test_kernel_dtb_collision(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate([(0, 0x20000000)], [], 0x4000000, 0x800000, 0, 0x4001000, 4096)

    def test_outside_ram(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            validate([(0, 0x20000000)], [], 0x20000000, 0x800000, 0, 0x5000000, 4096)

    def test_misaligned_kernel(self):
        with self.assertRaisesRegex(ValueError, "alignment"):
            validate([(0, 0x20000000)], [], 0x4001000, 0x800000, 0, 0x5000000, 4096)

    def test_kernel_below_text_offset(self):
        with self.assertRaisesRegex(ValueError, "offset"):
            validate([(0, 0x20000000)], [], 0, 0x800000, 0x200000, 0x5000000, 4096)

    def test_dtb_misaligned(self):
        with self.assertRaisesRegex(ValueError, "DTB"):
            validate([(0, 0x20000000)], [], 0x4000000, 0x800000, 0, 0x5000001, 4096)

    def test_dtb_too_large(self):
        with self.assertRaisesRegex(ValueError, "DTB"):
            validate([(0, 0x20000000)], [], 0x4000000, 0x800000, 0, 0x5000000, 0x200001)

    def test_dtb_reserved_collision(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            validate([(0, 0x20000000)], [(0x5000000, 0x5100000)],
                     0x4000000, 0x800000, 0, 0x5000000, 4096)

    def test_overflow(self):
        with self.assertRaisesRegex(ValueError, "overflow"):
            interval((1 << 64) - 4096, 8192)


if __name__ == "__main__":
    unittest.main()
