#!/usr/bin/env python3
"""Compare the MiniOLT optical I2C bridge with libasicdrv ARM64 execution."""
import argparse
import ctypes
import errno
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
    UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_LR,
    UC_ARM64_REG_PC, UC_ARM64_REG_SP)


class Registers:
    def __init__(self, status=0):
        self.values = {0x78: [status, 0], 0x79: [1, 0], 0x7c: [0xabcd]}
        self.events = []
        self.fail_at = 0

    def read(self, reg):
        data = self.values.get(reg, [0])
        value = data[0]
        if len(data) > 1:
            data.pop(0)
        self.events.append(['read', reg, value])
        return value

    def write(self, reg, value):
        self.events.append(['write', reg, value])


def stock(binary, registers, read, word):
    emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    with binary.open('rb') as stream:
        elf = ELFFile(stream)
        segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        first = min(s['p_vaddr'] for s in segments) & ~4095
        last = (max(s['p_vaddr'] + s['p_memsz'] for s in segments) + 4095) & ~4095
        emulator.mem_map(first, last - first)
        for segment in segments:
            emulator.mem_write(segment['p_vaddr'], segment.data())
        symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.dynsym').iter_symbols()}
    text = subprocess.check_output(['aarch64-linux-gnu-objdump', '-d', str(binary)], text=True)
    hooks = {int(address, 16): name for address, name in
             re.findall(r'^([0-9a-f]+) <([^<>]+)@plt>:', text, re.M)}
    scratch = 0x20000000
    emulator.mem_map(scratch, 0x10000)
    emulator.mem_write(scratch, b'\0\0')
    for reg, value in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                           UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5),
                          (0, 0xd0, 1, 2 if word else 1, 0x7f, scratch if read else 0x1234)):
        emulator.reg_write(reg, value)
    emulator.reg_write(UC_ARM64_REG_SP, scratch + 0xff00)
    emulator.reg_write(UC_ARM64_REG_LR, scratch + 0x1000)

    def hook(uc, address, size, unused):
        name = hooks.get(address)
        if name is None:
            return
        x0, x1 = uc.reg_read(UC_ARM64_REG_X0), uc.reg_read(UC_ARM64_REG_X1)
        result = 0
        if name == 'FPGA_reg_read':
            result = registers.read(x0)
        elif name == 'FPGA_reg_write':
            registers.write(x0, x1)
        elif name == 'open':
            result = 3
        elif name not in ('flock', 'close', 'usleep'):
            raise AssertionError(('unmodeled stock function', name))
        uc.reg_write(UC_ARM64_REG_X0, result)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    emulator.hook_add(UC_HOOK_CODE, hook)
    function = 'drv_fpga_exti2c_read_reg' if read else 'drv_fpga_exti2c_write_reg'
    emulator.emu_start(symbols[function], scratch + 0x1000, count=100000)
    assert emulator.reg_read(UC_ARM64_REG_PC) == scratch + 0x1000
    assert emulator.reg_read(UC_ARM64_REG_X0) == 0
    return struct.unpack('<H', emulator.mem_read(scratch, 2))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    read_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint))
    write_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
    delay_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint)

    class IO(ctypes.Structure):
        _fields_ = [('context', ctypes.c_void_p), ('read', read_fn), ('write', write_fn), ('delay', delay_fn)]

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'native.c').write_text('''#include <errno.h>
#include "rtl9617c-fttr-i2c.h"
int transfer(const struct rtl_fttr_reg_io *io, unsigned int *value, int word, int read) {
    return rtl_fttr_i2c_xfer(io, 0x68, 0x7f, value, word, read);
}
''')
        headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-I', str(headers),
                        str(path / 'native.c'), '-o', str(path / 'native.so')], check=True)
        library = ctypes.CDLL(str(path / 'native.so'))
        library.transfer.argtypes = [ctypes.POINTER(IO), ctypes.POINTER(ctypes.c_uint), ctypes.c_int, ctypes.c_int]

        def native(registers, read, word):
            def rd(context, reg, pointer):
                value = registers.read(reg)
                if len(registers.events) == registers.fail_at:
                    return -errno.EIO
                pointer[0] = value
                return 0

            def wr(context, reg, value):
                registers.write(reg, value)
                return -errno.EIO if len(registers.events) == registers.fail_at else 0

            io = IO(None, read_fn(rd), write_fn(wr), delay_fn(lambda *_: None))
            value = ctypes.c_uint(0 if read else 0x1234)
            return library.transfer(ctypes.byref(io), ctypes.byref(value), word, read), value.value

        cases = []
        for read in (False, True):
            for word in (False, True):
                for status in (0, 1):
                    expected, actual = Registers(status), Registers(status)
                    value = stock(args.stock, expected, read, word)
                    ret, data = native(actual, read, word)
                    assert ret == 0 and (not read or data == value)
                    assert expected.events == actual.events, (expected.events, actual.events)
                    cases.append({'read': read, 'word': word, 'stale_status': status,
                                  'stock_native_match': True, 'operations': len(actual.events)})
                    for fail_at in range(1, len(actual.events) + 1):
                        broken = Registers(status)
                        broken.fail_at = fail_at
                        assert native(broken, read, word)[0] == -errno.EIO
                        assert len(broken.events) == fail_at
        for stuck in (0x78, 0x79):
            registers = Registers()
            registers.values[stuck] = [1]
            assert native(registers, True, False)[0] == -errno.ETIMEDOUT
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'cases': cases, 'register_errors_propagated': True, 'busy_timeout': True,
        'hardware_verified': False}, indent=2) + '\n')
    print(f'{len(cases)} stock/native optical I2C comparisons passed; error and timeout checks passed')


if __name__ == '__main__':
    main()
