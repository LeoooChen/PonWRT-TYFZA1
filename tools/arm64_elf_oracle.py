"""Execute named ARM64 ELF functions with explicit external IO hooks."""
from pathlib import Path
import re
import struct
import subprocess

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
    UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6,
    UC_ARM64_REG_X7, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP)


class ElfOracle:
    def __init__(self, path, handler, overrides=()):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.scratch = 0x20000000
        self.uc.mem_map(self.scratch, 0x200000)
        self.symbols, self.hooks = {}, {}
        registers = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                     UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5)
        with Path(path).open('rb') as stream:
            elf = ELFFile(stream)
            segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
            first = min(s['p_vaddr'] for s in segments) & ~4095
            last = (max(s['p_vaddr'] + s['p_memsz'] for s in segments) + 4095) & ~4095
            self.uc.mem_map(first, last - first)
            for segment in segments:
                self.uc.mem_write(segment['p_vaddr'], segment.data())
            table = elf.get_section_by_name('.dynsym')
            self.symbols = {s.name: s['st_value'] for s in table.iter_symbols() if s['st_value']}
            external = {}
            for section in elf.iter_sections():
                if section['sh_type'] != 'SHT_RELA':
                    continue
                for rel in section.iter_relocations():
                    kind = rel['r_info_type']
                    if kind == 1027:
                        value = rel['r_addend']
                    elif kind in (257, 1025, 1026):
                        symbol = table.get_symbol(rel['r_info_sym'])
                        value = symbol['st_value']
                        if not value:
                            value = external.setdefault(symbol.name, self.scratch + 0x10000 + len(external) * 16)
                        value += rel['r_addend']
                    else:
                        raise AssertionError(('unmodeled ELF relocation', kind))
                    self.uc.mem_write(rel['r_offset'], struct.pack('<Q', value))
        disassembly = subprocess.check_output(['aarch64-linux-gnu-objdump', '-d', str(path)], text=True)
        self.hooks = {int(address, 16): name for address, name in
                      re.findall(r'^([0-9a-f]+) <([^<>]+)@plt>:', disassembly, re.M)}
        self.hooks.update({self.symbols[name]: name for name in overrides})

        def hook(uc, address, size, unused):
            name = self.hooks.get(address)
            if name is None:
                return
            if name in self.symbols and name not in overrides:
                uc.reg_write(UC_ARM64_REG_PC, self.symbols[name])
                return
            args = [uc.reg_read(r) for r in registers]
            uc.reg_write(UC_ARM64_REG_X0, handler(name, args) & ((1 << 64) - 1))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

        self.uc.hook_add(UC_HOOK_CODE, hook)

    def call(self, name, *args, instruction_budget=2000000):
        for register, value in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7), args):
            self.uc.reg_write(register, value)
        end = self.scratch + 0x1000
        self.uc.reg_write(UC_ARM64_REG_SP, self.scratch + 0x1ff000)
        self.uc.reg_write(UC_ARM64_REG_LR, end)
        self.uc.emu_start(self.symbols[name], end, count=instruction_budget)
        assert self.uc.reg_read(UC_ARM64_REG_PC) == end, (name, 'instruction budget exhausted')
        value = self.uc.reg_read(UC_ARM64_REG_X0)
        return value if value < 1 << 63 else value - (1 << 64)
