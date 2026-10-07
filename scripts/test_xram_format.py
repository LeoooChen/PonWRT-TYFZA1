#!/usr/bin/env python3
"""Check management-frame sizes and XRAM ring wrap against hardware formats."""
from pathlib import Path
import subprocess
import tempfile
import unittest

HEADER = Path(__file__).resolve().parents[1] / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram-format.h'


class XramFormat(unittest.TestCase):
    def test_packet_sizes_and_wrap(self):
        code = r'''
#include <assert.h>
#include "rtl9617c-xram-format.h"
int main(void) {
    /* Ethernet minimum, exact FCS length, and tagged MTU. */
    assert(rtl_xram_wire_length(42) == 64);
    assert(rtl_xram_wire_length(60) == 64);
    assert(rtl_xram_wire_length(61) == 65);
    assert(rtl_xram_wire_length(1514) == 1518);
    assert(rtl_xram_wire_length(1518) == 1522);
    /* One 8-byte XR header plus one 8-byte Header A, then frame+FCS. */
    assert(rtl_xram_tx_next(0x300, 64) == 0x30a);
    assert(rtl_xram_tx_next(0x3fc, 64) == 0x306);
    assert(rtl_xram_tx_next(0x300, 1520) == 0x3c0);
    assert(rtl_xram_tx_next(0x3ff, 1520) == 0x3bf);
    /* On the actual board: bits 11:10 are ignored; bit 12 changes tail length. */
    assert(rtl_xram_tx_header(0x30f, 102) == 0x8000630fU);
    assert(rtl_xram_tx_header(0x3c0, 1518) == 0x800063c0U);
    assert(rtl_xram_tx_header(0x30a, 64) == 0x8000030aU);
    assert(rtl_xram_word_next(0x17fc, 0, 0x1800) == 0);
    assert(rtl_xram_word_next(0x1ffc, 0x1800, 0x2000) == 0x1800);
    for (unsigned pointer = 0x300; pointer < 0x400; pointer++) {
        unsigned next = rtl_xram_tx_next(pointer, 1528);
        assert(next >= 0x300 && next < 0x400);
        assert(((next - pointer) & 0xff) == 193);
    }
}
'''
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'xram-format-test.c'
            binary = Path(temp) / 'xram-format-test'
            source.write_text(code)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=undefined',
                            '-I', str(HEADER.parent), str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
