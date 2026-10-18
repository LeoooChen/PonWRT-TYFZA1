#!/usr/bin/env python3
"""Compare the native USB LED phases with the supplied original kernel ELF."""
import ctypes
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP

uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
uc.mem_map(0xffffffc010000000, 0x2200000)
with Path(sys.argv[1]).open('rb') as stream:
    elf = ELFFile(stream)
    for segment in elf.iter_segments():
        if segment['p_type'] == 'PT_LOAD' and segment['p_filesz']:
            uc.mem_write(segment['p_vaddr'], segment.data())
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
uc.mem_map(0x5000000, 0x10000)
usb, led, devices, stop = 0x5002000, 0x5003000, [0x5004000, 0x5005000], 0x5008000


def put(address, value, fmt='Q'):
    uc.mem_write(address, struct.pack('<' + fmt, value))


def hook(uc, address, size, data):
    if address == symbols['led_set_brightness']:
        put(led + 8, uc.reg_read(UC_ARM64_REG_X1), 'I')
    elif address != symbols['mod_timer']:
        return
    uc.reg_write(UC_ARM64_REG_X0, 0)
    uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))


uc.hook_add(UC_HOOK_CODE, hook)
put(usb + 72, led)
put(usb + 80, 5, 'I')
put(usb + 280, 2, 'I')
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/leds/ledtrig-tyfza1-usb.c').read_text()
function = re.search(r'static bool tyfza1_usb_output\(.*?\n\}', source, re.S)[0]
shim = '''#include <stdbool.h>
#define BIT(n) (1U << (n))
struct tyfza1_usb_sample {unsigned int present, urbs[2];};
struct tyfza1_usb_led {unsigned int urbs[2]; bool on;};
static struct tyfza1_usb_led data;
'''
wrapper = '''
int tick(unsigned int mask, unsigned int a, unsigned int b) {
 struct tyfza1_usb_sample sample = {mask, {a,b}};
 data.on = tyfza1_usb_output(&data, &sample);
 return data.on;
}
'''
with tempfile.TemporaryDirectory(prefix='usb-led-') as directory:
    path = Path(directory) / 'test.c'
    path.write_text(shim + function + wrapper)
    library = path.with_suffix('.so')
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                    str(path), '-o', str(library)], check=True)
    native = ctypes.CDLL(str(library))
    cases = [(0,0,0), (1,10,0), (1,10,0), (1,10,0), (1,10,0),
             (1,11,0), (1,12,0), (1,13,0), (1,13,0), (0,13,0),
             (2,13,20), (2,13,20), (2,13,20), (3,14,21), (3,14,21),
             (2,14,21), (0,14,21)]
    for mask, a, b in cases:
        for port, count in enumerate((a, b)):
            put(usb + 128 + 48 * port, devices[port] if mask & (1 << port) else 0)
            put(devices[port] + 1280, count, 'I')
        uc.reg_write(UC_ARM64_REG_X0, usb + 8)
        uc.reg_write(UC_ARM64_REG_SP, 0x500ff00)
        uc.reg_write(UC_ARM64_REG_LR, stop)
        uc.emu_start(symbols['usbdev_trig_timer'], stop, count=10000)
        assert uc.reg_read(UC_ARM64_REG_PC) == stop
        brightness = struct.unpack('<I', uc.mem_read(led + 8, 4))[0]
        assert native.tick(mask, a, b) == bool(brightness), (mask, a, b, brightness)
print('USB LED: 17 presence/idle/traffic/removal phases match actual OEM ARM64 timer')
