#!/usr/bin/env python3
"""Compare GN25L99 calibration with the original initializer, without hardware."""
import argparse
import ctypes
import errno
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from trace_stock_fttr_bosa import trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = '''#include <errno.h>
#include "rtl9617c-fttr-bosa.h"
typedef int (*write_fn)(void *, unsigned int, unsigned char);
int configure(write_fn write, const unsigned char *cal, unsigned int size) {
    int ret = rtl_fttr_gn25l99_validate(cal, size, 0x68);
    return ret ? ret : rtl_fttr_gn25l99_calibrate(0, write, cal);
}
'''
    callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_ubyte)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'native.c').write_text(source)
        headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-I', str(headers),
                        str(path / 'native.c'), '-o', str(path / 'native.so')], check=True)
        native = ctypes.CDLL(str(path / 'native.so'))
        native.configure.argtypes = [callback, ctypes.c_char_p, ctypes.c_uint]
        operations = []
        fail_at = 0

        def write(context, reg, value):
            operations.append([0, 0xd0, reg, value])
            return -errno.EIO if len(operations) == fail_at else 0

        writer = callback(write)
        actual = args.calibration.read_bytes()
        pattern = bytearray((i * 73 + 11) & 255 for i in range(661))
        pattern[:5] = bytes([2, 149, 0, 10, 0xd0])
        checksum = 0
        for value in pattern:
            checksum ^= value
        pattern[2] = checksum
        cases = []
        for name, calibration in [('original', actual), ('pattern', bytes(pattern))]:
            operations.clear()
            assert native.configure(writer, calibration, len(calibration)) == 0
            stock = trace(args.stock, calibration)
            assert operations == stock['operations'] and stock['return'] == 0
            assert len(operations) == 128
            cases.append({'input': name, 'register_writes': 128, 'stock_native_match': True})
        broken = bytearray(actual)
        broken[2] ^= 1
        stock_broken = trace(args.stock, bytes(broken))
        assert stock_broken['return'] == 0 and len(stock_broken['operations']) == 128
        operations.clear()
        assert native.configure(writer, bytes(broken), len(broken)) == -errno.EBADMSG
        assert not operations
        assert native.configure(writer, actual[:156], 156) == -errno.EMSGSIZE
        fail_at = 65
        assert native.configure(writer, actual, len(actual)) == -errno.EIO
        assert len(operations) == fail_at
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'binary_sha256': stock['binary_sha256'],
        'calibration_sha256': hashlib.sha256(actual).hexdigest(), 'cases': cases,
        'stock_checksum_bug_reproduced': True, 'native_bad_checksum_stops_before_io': True,
        'io_error_stops_calibration': True, 'hardware_verified': False}, indent=2) + '\n')
    print('GN25L99: 2 original/native 128-write sequences match; corrupt checksum and IO failure stop')


if __name__ == '__main__':
    main()
