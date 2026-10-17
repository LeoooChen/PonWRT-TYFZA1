#!/usr/bin/env python3
"""Compare 9690 host/PON SerDes IO against actual libasicdrv execution."""
import argparse
import ctypes
import errno
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_elf_oracle import ElfOracle


class Registers:
    def __init__(self, seed=0, stuck=None, fail_at=0):
        self.seed, self.stuck, self.fail_at = seed, stuck, fail_at
        self.values, self.events = {}, []

    def read(self, reg):
        value = self.values.get(reg, self.seed)
        if reg == 0xc08008:
            value |= 1 << 6
        if reg == 0xc0c5c0:
            value |= 0xf << 22
        if self.stuck and reg == self.stuck[0]:
            value &= ~(1 << self.stuck[1])
        self.events.append(['read', reg, value])
        return value

    def write(self, reg, value):
        self.events.append(['write', reg, value])
        self.values[reg] = value

    def delay(self, usec):
        self.events.append(['delay', usec])


def stock(binary, registers, kind, mode, nway):
    def handler(name, args):
        if name == 'FPGA_reg_read':
            return registers.read(args[0])
        if name == 'FPGA_reg_write':
            registers.write(*args[:2])
        elif name == 'usleep':
            registers.delay(args[0])
        elif name == 'rtk_switch_version_get':
            for ptr, value in zip(args, (0x8277c000, 67, 0)):
                oracle.uc.mem_write(ptr, struct.pack('<I', value))
        elif name == 'drv_get_miniolt_chiptype':
            return 3
        elif name == 'open':
            return 3
        elif name not in ('flock', 'close', 'printf', 'puts'):
            raise AssertionError(('unmodeled external function', name))
        return 0

    oracle = ElfOracle(binary, handler, ('FPGA_reg_read', 'FPGA_reg_write',
                                        'drv_get_miniolt_chiptype'))
    if kind == 'host':
        assert oracle.call('drv_reset_soft', 0) == 0
        registers.delay(100)  # onuinfo caller, between reset assertions
        assert oracle.call('drv_reset_soft', 1) == 0
        assert oracle.call('drv_set_reg_otp', 6 if mode == 5 else 7) == 0
        if mode == 9:
            assert oracle.call('drv_set_pause_en', 1) == 0
        return oracle.call('WTG_serdes_init', 1 if mode == 5 else 3, nway)
    if mode == 2:
        assert oracle.call('drv_set_us_phy_mode', 1) == 0
        assert oracle.call('drv_set_sds_us_speed', 2) == 0
    return oracle.call('OLT_ponserdes_init', mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    read_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.POINTER(ctypes.c_uint))
    write_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
    delay_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint)

    class IO(ctypes.Structure):
        _fields_ = [('context', ctypes.c_void_p), ('read', read_fn),
                    ('write', write_fn), ('delay', delay_fn)]

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'native.c').write_text('''#include <stdbool.h>
#include <errno.h>
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U >> (31-(h))) & (~0U << (l)))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#include "rtl9617c-fttr-serdes.h"
int configure(const struct rtl_fttr_reg_io *io, int host, unsigned int mode, int nway) {
 return host ? rtl_fttr_wtg_configure(io, mode, nway) : rtl_fttr_pon_configure(io, mode);
}
''')
        headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-I', str(headers), str(path / 'native.c'), '-o', str(path / 'native.so')], check=True)
        library = ctypes.CDLL(str(path / 'native.so'))
        library.configure.argtypes = [ctypes.POINTER(IO), ctypes.c_int, ctypes.c_uint, ctypes.c_int]

        def native(registers, kind, mode, nway):
            def rd(context, reg, pointer):
                pointer[0] = registers.read(reg)
                return -errno.EIO if len(registers.events) == registers.fail_at else 0

            def wr(context, reg, value):
                registers.write(reg, value)
                return -errno.EIO if len(registers.events) == registers.fail_at else 0

            io = IO(None, read_fn(rd), write_fn(wr),
                    delay_fn(lambda context, usec: registers.delay(usec)))
            return library.configure(ctypes.byref(io), kind == 'host', mode, nway)

        cases = []
        modes = [('host', m, n) for m in (5, 9) for n in (0, 1)]
        modes += [('pon', m, 0) for m in (1, 2, 3)]
        for kind, mode, nway in modes:
            for seed in (0, 0xffffffff):
                expected, actual = Registers(seed), Registers(seed)
                assert stock(args.stock, expected, kind, mode, nway) == 0
                assert native(actual, kind, mode, nway) == 0
                a, b = expected.events, actual.events
                assert a == b, (kind, mode, nway, hex(seed), next(
                    ((i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y),
                    ('length', len(a), len(b))))
                cases.append({'kind': kind, 'mode': mode, 'nway': nway,
                              'seed': hex(seed), 'operations': len(a), 'stock_native_match': True})

        for kind, mode, reg, bit, attempts in [('host', 9, 0xc08008, 6, 2001),
                ('pon', 3, 0xc0c5c0, 25, 101), ('pon', 3, 0xc0c5c0, 24, 101),
                ('pon', 3, 0xc0c5c0, 23, 501), ('pon', 3, 0xc0c5c0, 22, 501)]:
            actual = Registers(stuck=(reg, bit))
            assert native(actual, kind, mode, 1) == -errno.ETIMEDOUT
            # No final reset release after a failed hardware lock.
            assert not actual.values.get(0xc080f8, 0) & (0xa if kind == 'host' else 5)
            expected = Registers(stuck=(reg, bit))
            assert stock(args.stock, expected, kind, mode, 1) == 0  # original bug
            assert sum(e[0] == 'read' and e[1] == reg for e in actual.events) == (
                attempts + {25: 0, 24: 1, 23: 2, 22: 3}.get(bit, 0))

        for kind, mode in [('host', 9), ('pon', 3)]:
            good = Registers()
            assert native(good, kind, mode, 1) == 0
            for operation in ('read', 'write'):
                fail_at = next(i for i, e in enumerate(good.events, 1) if e[0] == operation)
                broken = Registers(fail_at=fail_at)
                assert native(broken, kind, mode, 1) == -errno.EIO
                assert len(broken.events) == fail_at

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'cases': cases, 'original_pll_timeout_returns_success': True,
        'native_pll_timeout_stops': True, 'transport_error_propagated': True,
        'hardware_verified': False, 'scope': 'raw register IO and modeled delays'}, indent=2) + '\n')
    print(f'Peer SerDes: {len(cases)} original/native IO sequences match; PLL/error checks passed')


if __name__ == '__main__':
    main()
