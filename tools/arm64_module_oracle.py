"""Run selected relocatable ARM64 module functions with explicit IO callbacks."""
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_SP_EL0


class ModuleOracle:
    def __init__(self, module, names, handler):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.sections, self.symbols, self.functions, self.hooks = {}, {}, {}, {}
        self.trampoline, self.scratch = 0x4000000, 0x5000000
        self.uc.mem_map(self.trampoline, 0x10000)
        self.uc.mem_map(self.scratch, 0x10000)
        base = 0x1000000
        with Path(module).open('rb') as stream:
            elf = ELFFile(stream)
            for index, section in enumerate(elf.iter_sections()):
                if not section['sh_flags'] & 2 or not section['sh_size']:
                    continue
                size = (section['sh_size'] + 4095) & ~4095
                self.sections[index] = base
                self.uc.mem_map(base, size)
                if section['sh_type'] != 'SHT_NOBITS':
                    self.uc.mem_write(base, section.data())
                base += size
            symbols = elf.get_section_by_name('.symtab')
            for symbol in symbols.iter_symbols():
                if symbol['st_shndx'] in self.sections and symbol.name:
                    self.symbols[symbol.name] = (self.sections[symbol['st_shndx']] + symbol['st_value'], symbol['st_size'])
            selected = [symbols.get_symbol_by_name(name)[0] for name in names]
            self.functions = {name: self.symbols[name][0] for name in names}
            for relsection in elf.iter_sections():
                if relsection['sh_type'] != 'SHT_RELA' or relsection['sh_info'] not in self.sections:
                    continue
                is_code = bool(elf.get_section(relsection['sh_info'])['sh_flags'] & 4)
                for rel in relsection.iter_relocations():
                    offset = rel['r_offset']
                    if is_code and not any(s['st_shndx'] == relsection['sh_info'] and
                                           s['st_value'] <= offset < s['st_value'] + s['st_size'] for s in selected):
                        continue
                    symbol = symbols.get_symbol(rel['r_info_sym'])
                    kind = rel['r_info_type']
                    if not is_code and kind != 257:
                        continue
                    if kind == 283 and symbol.name not in names:
                        value = self.callback(symbol.name)
                    elif symbol['st_shndx'] in self.sections:
                        value = self.sections[symbol['st_shndx']] + symbol['st_value'] + rel['r_addend']
                    elif not is_code:
                        continue
                    else:
                        raise AssertionError(('Unmodeled relocation', kind, symbol.name))
                    place = self.sections[relsection['sh_info']] + offset
                    if kind == 257:
                        self.word(place, value)
                        continue
                    instruction = self.unpack(place, 'I')[0]
                    if kind == 275:
                        imm = ((value >> 12) - (place >> 12)) & 0x1fffff
                        instruction = (instruction & ~0x60ffffe0) | (imm & 3) << 29 | (imm >> 2) << 5
                    elif kind in (277, 278, 284, 285, 286):
                        shift = {277: 0, 278: 0, 284: 1, 285: 2, 286: 3}[kind]
                        instruction = (instruction & ~(0xfff << 10)) | ((value & 0xfff) >> shift) << 10
                    elif kind in (282, 283):
                        instruction = (instruction & ~0x3ffffff) | ((value - place) >> 2 & 0x3ffffff)
                    else:
                        raise AssertionError(('Unsupported relocation', kind, symbol.name))
                    self.uc.mem_write(place, struct.pack('<I', instruction))
        self.uc.reg_write(UC_ARM64_REG_SP_EL0, self.scratch + 0x9000)

        def hook(uc, address, size, data):
            if address not in self.hooks:
                return
            args = [uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3)]
            uc.reg_write(UC_ARM64_REG_X0, handler(self.hooks[address], args))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
        self.uc.hook_add(UC_HOOK_CODE, hook)

    def callback(self, name):
        address = self.trampoline + len(self.hooks) * 16
        self.hooks[address] = name
        return address

    def word(self, address, value):
        self.uc.mem_write(address, struct.pack('<Q', value))

    def unpack(self, address, format):
        return struct.unpack('<' + format, self.uc.mem_read(address, struct.calcsize('<' + format)))

    def call(self, name, *args):
        for register, value in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3), args):
            self.uc.reg_write(register, value)
        end = self.scratch + 0x8000
        self.uc.reg_write(UC_ARM64_REG_SP, self.scratch + 0xff00)
        self.uc.reg_write(UC_ARM64_REG_LR, end)
        self.uc.emu_start(self.functions[name], end, count=100000)
        assert self.uc.reg_read(UC_ARM64_REG_PC) == end, name
        return self.uc.reg_read(UC_ARM64_REG_X0)
