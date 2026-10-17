#!/usr/bin/env python3
"""Compare native register transactions against the actual stock ARM64 module."""
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
from trace_stock_fttr_mdio import StockMdio


source = r'''
#include <errno.h>
#include "rtl9617c-fttr-mdio.h"
int transfer(const struct rtl_fttr_mdio_io *io, int backend, unsigned int reg,
             unsigned int *value, int write) {
    return rtl_fttr_reg_xfer(io, backend, reg, value, write);
}
int identify(const struct rtl_fttr_mdio_io *io, int *backend) {
    return rtl_fttr_identify(io, (enum rtl_fttr_backend *)backend);
}
'''

read_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
write_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_ushort)
delay_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint)


class IO(ctypes.Structure):
    _fields_ = [('context', ctypes.c_void_p), ('read', read_fn),
                ('write', write_fn), ('delay', delay_fn)]


class NativeMdio:
    def __init__(self, library, values, fail_at=0):
        self.library = library
        self.values = {reg: list(data) for reg, data in values.items()}
        self.events, self.address, self.low, self.read_value = [], 0, 0, 0
        self.fail_at = fail_at
        self.io = IO(None, read_fn(self.read), write_fn(self.write), delay_fn(lambda *_: None))

    def read(self, context, reg):
        value = (self.read_value >> (16 if reg == 21 else 0)) & 0xffff
        self.events.append(['read', reg, value])
        return -errno.EIO if len(self.events) == self.fail_at else value

    def write(self, context, reg, value):
        self.events.append(['write', reg, value])
        if len(self.events) == self.fail_at:
            return -errno.EIO
        if reg == 16:
            self.address = value
        elif reg == 17:
            self.address |= (value & 0x7fff) << 16
            if value & 0x8000:
                values = self.values.get(self.address, [0])
                self.read_value = values[0]
                if len(values) > 1:
                    values.pop(0)
        elif reg == 18:
            self.low = value
        elif reg == 19:
            self.values[self.address] = [value << 16 | self.low]
        return 0

    def transfer(self, backend, reg, value=0, write=False):
        data = ctypes.c_uint(value)
        result = self.library.transfer(ctypes.byref(self.io), backend, reg,
                                       ctypes.byref(data), write)
        return result, data.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    cases = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'native.c').write_text(source)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-I', str(headers), str(path / 'native.c'), '-o', str(path / 'native.so')], check=True)
        library = ctypes.CDLL(str(path / 'native.so'))
        library.transfer.argtypes = [ctypes.POINTER(IO), ctypes.c_int, ctypes.c_uint,
                                      ctypes.POINTER(ctypes.c_uint), ctypes.c_int]
        library.identify.argtypes = [ctypes.POINTER(IO), ctypes.POINTER(ctypes.c_int)]
        for backend in (1, 2, 3):
            for reg, ready in ((0xc00044, 0), (0x40400, 2), (0x408ff, 1), (0x41001, 4)):
                for write in (False, True):
                    result_reg = {2: 0x40072, 1: 0x40073, 4: 0x40074}.get(ready, 0)
                    values = {reg: [0xdeadbeef], 0x40040: [0, ready], result_reg: [0x87654321]}
                    native = NativeMdio(library, values)
                    stock = StockMdio(args.stock, backend, values)
                    actual = native.transfer(backend, reg, 0x12345678, write)
                    expected = stock.transfer(reg, 0x12345678, write)
                    assert actual == expected, (backend, hex(reg), write, actual, expected)
                    assert native.events == stock.events, (backend, hex(reg), write, native.events, stock.events)
                    cases.append({'backend': backend, 'reg': hex(reg), 'write': write,
                                  'stock_native_match': True, 'mdio_operations': len(native.events)})
                    for fail_at in range(1, len(native.events) + 1):
                        broken = NativeMdio(library, values, fail_at)
                        ret, _ = broken.transfer(backend, reg, 0x12345678, write)
                        assert ret == -errno.EIO and len(broken.events) == fail_at
            for write in (False, True):
                native = NativeMdio(library, {})
                assert native.transfer(backend, 0x40400, write=write)[0] == -errno.ETIMEDOUT
        for value, backend in ((0x9690, 3), (0, 1), (1, 1), (2, 2), (0xffffffff, -errno.ENODEV)):
            native = NativeMdio(library, {0xc00044: [value if value == 0x9690 else 0], 3: [value]})
            actual_backend = ctypes.c_int()
            ret = library.identify(ctypes.byref(native.io), ctypes.byref(actual_backend))
            assert (ret if ret else actual_backend.value) == backend
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'scope': 'offline stock machine code versus native transport; hardware untested',
        'cases': cases, 'errno_propagation': True, 'table_timeout': True}, indent=2) + '\n')
    print(f'{len(cases)} stock/native MDIO comparisons passed; error and timeout checks passed')


if __name__ == '__main__':
    main()
