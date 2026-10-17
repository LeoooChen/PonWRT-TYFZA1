#!/usr/bin/env python3
"""Execute the shipping GN25L99 initializer with in-memory optical/file IO."""
import hashlib
from pathlib import Path
import re
import subprocess

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
    UC_ARM64_REG_X3, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP)


def trace(binary, calibration):
    binary = Path(binary)
    emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    with binary.open('rb') as stream:
        elf = ELFFile(stream)
        segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        first = min(s['p_vaddr'] for s in segments) & ~4095
        last = (max(s['p_vaddr'] + s['p_memsz'] for s in segments) + 4095) & ~4095
        emulator.mem_map(first, last - first)
        for segment in segments:
            emulator.mem_write(segment['p_vaddr'], segment.data())
    # This stripped binary's GN25L99 initializer is 0x40aa24..0x40adf0.
    # The normal main initializes its optical slave address before dispatch.
    emulator.mem_write(0x426148, bytes([calibration[4]]))
    text = subprocess.check_output(['aarch64-linux-gnu-objdump', '-d', str(binary)], text=True)
    hooks = {int(address, 16): name for address, name in
             re.findall(r'^([0-9a-f]+) <([^<>]+)@plt>:', text, re.M)}
    scratch = 0x20000000
    emulator.mem_map(scratch, 0x20000)
    emulator.mem_write(scratch, b'calibration.bin\0')
    emulator.reg_write(UC_ARM64_REG_X0, scratch)
    emulator.reg_write(UC_ARM64_REG_X1, 1)
    emulator.reg_write(UC_ARM64_REG_SP, scratch + 0x1ff00)
    emulator.reg_write(UC_ARM64_REG_LR, scratch + 0x1000)
    operations = []

    def hook(uc, address, size, unused):
        name = hooks.get(address)
        if name is None:
            return
        args = [uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                         UC_ARM64_REG_X2, UC_ARM64_REG_X3)]
        result = 0
        if name == 'fopen64':
            result = scratch + 0x2000
        elif name == 'fread':
            data = calibration[:args[1] * args[2]]
            uc.mem_write(args[0], data)
            result = len(data) // args[1]
        elif name == 'fpga_i2c_write_byte':
            operations.append([args[0], args[1], args[2], args[3]])
        elif name not in ('fseek', 'fclose', 'puts', 'printf'):
            raise AssertionError(('unmodeled original call', name, args))
        uc.reg_write(UC_ARM64_REG_X0, result)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    emulator.hook_add(UC_HOOK_CODE, hook)
    emulator.emu_start(0x40aa24, scratch + 0x1000, count=100000)
    assert emulator.reg_read(UC_ARM64_REG_PC) == scratch + 0x1000
    return {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'initializer': '0x40aa24', 'operations': operations,
            'return': emulator.reg_read(UC_ARM64_REG_X0)}
