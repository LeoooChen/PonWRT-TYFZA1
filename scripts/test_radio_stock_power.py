#!/usr/bin/env python3
"""Execute original AP power and FV machine code in modeled ARM64 memory."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_SP_EL0

header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/power.h'
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = [f'mac_pwr_{op}_ap_pcie_{chip}' for chip in ('8852c', '8192xb') for op in ('on', 'off')] + ['get_fv_info']
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
sections, functions, hooks = {}, {}, {}
base, tramp, scratch = 0x1000000, 0x4000000, 0x5000000
uc.mem_map(tramp, 0x10000)
uc.mem_map(scratch, 0x10000)

def callback(name):
    address = tramp + len(hooks) * 16
    hooks[address] = name
    return address

with module.open('rb') as stream:
    elf = ELFFile(stream)
    for index, section in enumerate(elf.iter_sections()):
        if not section['sh_flags'] & 2 or not section['sh_size']:
            continue
        size = (section['sh_size'] + 4095) & ~4095
        sections[index] = base
        uc.mem_map(base, size)
        if section['sh_type'] != 'SHT_NOBITS':
            uc.mem_write(base, section.data())
        base += size
    symbols = elf.get_section_by_name('.symtab')
    for name in names:
        symbol = symbols.get_symbol_by_name(name)[0]
        functions[name] = (sections[symbol['st_shndx']] + symbol['st_value'], symbol)
    for relsection in elf.iter_sections():
        if relsection['sh_type'] != 'SHT_RELA':
            continue
        for relocation in relsection.iter_relocations():
            offset = relocation['r_offset']
            if not any(s['st_shndx'] == relsection['sh_info'] and
                       s['st_value'] <= offset < s['st_value'] + s['st_size'] for _, s in functions.values()):
                continue
            symbol = symbols.get_symbol(relocation['r_info_sym'])
            place = sections[relsection['sh_info']] + offset
            kind = relocation['r_info_type']
            if kind == 283:
                value = callback(symbol.name)
            else:
                value = sections[symbol['st_shndx']] + symbol['st_value'] + relocation['r_addend']
            instruction = struct.unpack('<I', uc.mem_read(place, 4))[0]
            if kind == 275:
                immediate = ((value >> 12) - (place >> 12)) & 0x1fffff
                instruction = (instruction & ~0x60ffffe0) | (immediate & 3) << 29 | (immediate >> 2) << 5
            elif kind in (277, 284, 285, 286):
                shift = {277: 0, 284: 1, 285: 2, 286: 3}[kind]
                instruction = (instruction & ~(0xfff << 10)) | ((value & 0xfff) >> shift) << 10
            elif kind == 283:
                instruction = (instruction & ~0x3ffffff) | ((value - place) >> 2 & 0x3ffffff)
            else:
                raise AssertionError((kind, symbol.name))
            uc.mem_write(place, struct.pack('<I', instruction))

def word(address, value):
    uc.mem_write(address, struct.pack('<Q', value))

word(scratch, scratch + 0x1000)  # adapter -> MAC ops -> interface ops
word(scratch + 0x1000, scratch + 0x2000)
word(scratch + 24, scratch + 0x3000)
word(scratch + 88, scratch + 0x4000)
for index, name in enumerate(('read8', 'write8', 'read16', 'write16', 'read32', 'write32')):
    word(scratch + 0x2000 + index * 8, callback(name))
word(scratch + 0x3000 + 120, callback('delay_ms'))
word(scratch + 0x3000 + 176, callback('log'))
word(scratch + 0x1000 + 1704, callback('efuse'))
uc.reg_write(UC_ARM64_REG_SP_EL0, scratch + 0x9000)
registers, efuse, trace = bytearray(65536), bytearray(1536), []
chip_id = 2

def read(width, reg):
    return int.from_bytes(registers[reg:reg + width], 'little')

def write(width, reg, value):
    value &= (1 << (width * 8)) - 1
    registers[reg:reg + width] = value.to_bytes(width, 'little')

def poll(reg, mask, expected):
    trace.append(('poll', reg, mask, expected))
    write(4, reg, read(4, reg) & ~mask | expected)

def original_hook(emulator, address, size, data):
    if address not in hooks:
        return
    name = hooks[address]
    a, b, c = [emulator.reg_read(r) for r in (UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3)]
    result = 0
    if name.startswith('read'):
        result = read(int(name[4:]) // 8, a)
    elif name.startswith('write'):
        width = int(name[5:]) // 8
        if a != 0x2dc:  # The original LED1 write is an unchanged-value no-op.
            trace.append(('write', width, a, b & ((1 << (width * 8)) - 1)))
        write(width, a, b)
    elif name == 'pwr_poll_u32':
        poll(a, b, c)
    elif name == 'mac_write_xtal_si':
        trace.append(('xtal', a & 255, b & 255, c & 255))
    elif name == 'mac_read_efuse_plus' or name == 'efuse':
        assert a + b <= len(efuse)
        emulator.mem_write(c, bytes(efuse[a:a + b]))
        trace.extend(('efuse', offset, efuse[offset]) for offset in range(a, a + b))
    elif name == 'delay_ms':
        trace.append(('delay', a * 1000))
    elif name == 'is_chip_id':
        result = int(a == chip_id)
    elif name != 'log':
        raise AssertionError(('unexpected original callback', name))
    emulator.reg_write(UC_ARM64_REG_X0, result)
    emulator.reg_write(UC_ARM64_REG_PC, emulator.reg_read(UC_ARM64_REG_LR))

uc.hook_add(UC_HOOK_CODE, original_hook)

def original(name):
    uc.reg_write(UC_ARM64_REG_X0, scratch)
    uc.reg_write(UC_ARM64_REG_SP, scratch + 0xff00)
    uc.reg_write(UC_ARM64_REG_LR, scratch + 0x8000)
    uc.emu_start(functions[name][0], scratch + 0x8000, count=10000)
    assert uc.reg_read(UC_ARM64_REG_PC) == scratch + 0x8000
    return uc.reg_read(UC_ARM64_REG_X0)

R8, R16, R32 = [C.CFUNCTYPE(t, C.c_void_p, C.c_uint) for t in (C.c_ubyte, C.c_ushort, C.c_uint)]
W8, W16, W32 = [C.CFUNCTYPE(None, C.c_void_p, C.c_uint, t) for t in (C.c_ubyte, C.c_ushort, C.c_uint)]
DELAY = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)
POLL = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.POINTER(C.c_uint))

class IO(C.Structure):
    _fields_ = [('ctx', C.c_void_p)] + list(zip(('r8', 'r16', 'r32', 'w8', 'w16', 'w32', 'delay', 'poll'),
                                             (R8, R16, R32, W8, W16, W32, DELAY, POLL)))

class Calibration(C.Structure):
    _fields_ = [('tag', C.c_ubyte), ('process', C.c_ubyte)]

class Identity(C.Structure):
    _fields_ = [('hardware_cv', C.c_ubyte), ('cv', C.c_ubyte), ('factory', C.c_ubyte), ('adie', C.c_ushort)]

in_efuse, efuse_address = False, 0

def native_write(width, reg, value):
    global in_efuse, efuse_address
    if width == 1 and reg == 0xcc and value & 4:
        in_efuse = True
    if in_efuse:
        if reg == 0x30:
            efuse_address = value >> 16
        if width == 1 and reg == 0xcc and not value & 4:
            trace.append(('efuse', efuse_address, efuse[efuse_address]))
            in_efuse = False
    elif reg == 0x270:
        trace.append(('xtal', value & 255, value >> 8 & 255, value >> 16 & 255))
    else:
        trace.append(('write', width, reg, value))
    write(width, reg, value)

def native_poll(ctx, reg, mask, expected, delay, timeout, last):
    if reg == 4:
        poll(reg, mask, expected)
        last[0] = read(4, reg)
    elif reg == 0x270:
        last[0] = read(4, reg) & ~(1 << 31)
    elif reg == 0x30:
        last[0] = read(4, reg) | (1 << 29) | efuse[efuse_address]
    else:
        raise AssertionError(reg)
    return 0

callbacks = [R8(lambda c, r: read(1, r)), R16(lambda c, r: read(2, r)), R32(lambda c, r: read(4, r)),
             W8(lambda c, r, v: native_write(1, r, v)), W16(lambda c, r, v: native_write(2, r, v)),
             W32(lambda c, r, v: native_write(4, r, v)),
             DELAY(lambda c, us: None if in_efuse else trace.append(('delay', us))), POLL(native_poll)]
io = IO(None, *callbacks)
rng, count = random.Random(0x9617c), 0
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-stock-power-') as directory:
    work = Path(directory)
    source, binary = work / 'native.c', work / 'native.so'
    source.write_text('#include "power.h"\nint on(const struct rtl_ax_io *io,unsigned int chip,struct rtl_ax_calibration *cal){return rtl_ax_ap_power_on(io,chip,cal);}\nint off(const struct rtl_ax_io *io){return rtl_ax_ap_power_off(io);}\nvoid identity(unsigned int chip,unsigned char fv,unsigned short aid,struct rtl_ax_identity *out){rtl_ax_decode_identity(chip,fv,aid,out);}\n')
    subprocess.run(['cc', '-O2', '-shared', '-fPIC', '-I' + str(header.parent), str(source), '-o', str(binary)], check=True)
    native = C.CDLL(str(binary))
    native.on.argtypes = [C.POINTER(IO), C.c_uint, C.POINTER(Calibration)]
    native.off.argtypes = [C.POINTER(IO)]
    native.identity.argtypes = [C.c_uint, C.c_ubyte, C.c_ushort, C.POINTER(Identity)]
    for chip, family in ((0x52, '8852c'), (0x53, '8192xb')):
        for tag in (0xaa, 0xff):
            for process in (0, 255):
                efuse[0x5e9], efuse[0x5ea] = tag, process
                for vector in range(64):
                    initial = rng.randbytes(65536)
                    for operation in ('on', 'off'):
                        registers[:] = initial
                        trace.clear()
                        assert not original(f'mac_pwr_{operation}_ap_pcie_{family}')
                        expected = list(trace)
                        registers[:] = initial
                        trace.clear()
                        cal = Calibration()
                        assert not (native.on(C.byref(io), chip, C.byref(cal)) if operation == 'on' else native.off(C.byref(io)))
                        assert trace == expected, (family, operation, tag, process, vector, trace, expected)
                        count += 1
    identities = 0
    for chip, chip_id in ((0x52, 2), (0x53, 3)):
        for cv in range(16):
            for fv in range(256):
                efuse[0x5ea] = fv
                uc.mem_write(scratch + 0x4000, bytes([chip_id, 0, cv, 0, 255]))
                trace.clear()
                original('get_fv_info')
                expected = bytes(uc.mem_read(scratch + 0x4000, 5))
                identity = Identity(hardware_cv=cv)
                native.identity(chip, fv, 0x1234, C.byref(identity))
                factory = ord('?') if expected[4] == 255 else ord('TUS'[expected[4]])
                assert (identity.cv, identity.factory) == (expected[2], factory), (chip, cv, fv)
                identities += 1
report = {'stock_module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'native_source_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
          'stock_machine_code_power_trace_vectors': count, 'stock_CV_FV_vectors': identities,
          'GPIO_MAC_XTAL_power_and_XB_calibration_match': True,
          '8852C_has_no_SDK_added_voltage_calibration': True,
          'zero_FV_retains_hardware_CV': True,
          'efuse_read_window_tested_separately_against_GPL': True,
          'original_module_loaded_on_hardware': False, 'hardware_verified': False}
(root / 'logs/radio-stock-power-test.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
