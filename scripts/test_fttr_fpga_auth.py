#!/usr/bin/env python3
"""Compare original FPGA authentication, version policy and factory MIB conversion."""
import argparse
import ctypes as C
import errno
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_elf_oracle import ElfOracle
from elftools.elf.elffile import ELFFile
from test_fttr_fpga_boot import function


class PrefixComplete(Exception):
    pass


class Stock:
    def __init__(self, asic, onuinfo, mib):
        self.events, self.backend, self.version, self.result = [], 1, 0, 0
        self.asic = ElfOracle(asic, self.io, ('FPGA_reg_read', 'FPGA_reg_write'))
        with onuinfo.open('rb') as stream:
            elf = ELFFile(stream)
            symbols = elf.get_section_by_name('.dynsym')
            copies = {symbols.get_symbol(r['r_info_sym']).name: bytes(symbols.get_symbol(r['r_info_sym'])['st_size'])
                for section in elf.iter_sections() if section['sh_type'] == 'SHT_RELA'
                for r in section.iter_relocations() if r['r_info_type'] == 1024}
        self.onu = ElfOracle(onuinfo, self.io, copy_symbols=copies)
        self.onu.symbols['startup'] = 0x4122f8
        self.onu.hooks[0x412190] = 'print_versions'
        self.mib = ElfOracle(mib, self.mib_io)
        self.mib.uc.mem_write(self.mib.scratch + 0x8000, struct.pack('<Q', self.mib.scratch + 0x9000))
        for c in b'0123456789abcdefABCDEF':
            self.mib.uc.mem_write(self.mib.scratch + 0x9000 + c * 2, struct.pack('<H', 0x1000))

    def io(self, name, a):
        if name == 'FPGA_reg_write': self.events.append(['write', a[0], a[1]])
        elif name == 'FPGA_reg_read':
            self.events.append(['read', a[0], self.result]); return self.result
        elif name == 'drv_get_miniolt_chiptype': return self.backend
        elif name == 'drv_get_fpga_version': self.onu.uc.mem_write(a[0], struct.pack('<I', self.version))
        elif name == 'drv_set_auth_ciphertext':
            ptr = self.asic.scratch + 0x2000
            self.asic.uc.mem_write(ptr, bytes(self.onu.uc.mem_read(a[0], 32)))
            return self.asic.call(name, ptr)
        elif name == 'drv_get_auth_result':
            ptr = self.asic.scratch + 0x2000
            self.asic.call(name, ptr)
            self.onu.uc.mem_write(a[0], bytes(self.asic.uc.mem_read(ptr, 4)))
        elif name == 'usleep': self.events.append(['delay', a[0]])
        elif name == 'fopen64': raise PrefixComplete()
        elif name != 'print_versions': raise AssertionError(name)
        return 0

    def mib_io(self, name, a):
        u = self.mib.uc
        def string(address): return bytes(u.mem_read(address, 256)).split(b'\0')[0]
        if name == 'malloc': return self.mib.scratch + 0x6000
        if name == 'memset': u.mem_write(a[0], bytes([a[1] & 255]) * a[2]); return a[0]
        if name == 'memcpy': u.mem_write(a[0], bytes(u.mem_read(a[1], a[2]))); return a[0]
        if name == 'strlen': return len(string(a[0]))
        if name == 'strtoul': return int(string(a[0]), a[2])
        if name == '__ctype_b_loc': return self.mib.scratch + 0x8000
        if name == 'free': return 0
        raise AssertionError(name)

    def convert(self, text):
        src, dst = self.mib.scratch + 0x2000, self.mib.scratch + 0x3000
        self.mib.uc.mem_write(src, text.encode() + b'\0')
        assert self.mib.call('string_to_mib', dst, src, 13, 32) == 0
        return bytes(self.mib.uc.mem_read(dst, 32))

    def startup(self, backend, version, result, ciphertext):
        self.backend, self.version, self.result, self.events = backend, version, result, []
        self.onu.uc.mem_write(0x429c1c, ciphertext)
        try: self.onu.call('startup')
        except PrefixComplete: pass
        else: raise AssertionError('Original startup prefix not reached')
        return list(self.events)


def factory_cases(args, folder, stock):
    source, output = folder / 'input', folder / 'output'
    source.mkdir(); output.mkdir()
    for name in ('config_hs.xml', 'config.xml', 'rtkbosa_k.bin', 'rtkoltbosa_k.bin'):
        original = args.factory / name
        if original.exists(): shutil.copyfile(original, source / name)
    hs = (source / 'config_hs.xml').read_bytes()
    key = b'Name="HW_OLTFPGA_CIPHERTEXT" Value="'
    hs = re.sub(key + rb'[^\"]*"', key + b'PLACEHOLDER"', hs)
    if b'PLACEHOLDER' not in hs: hs += b'<Value ' + key + b'PLACEHOLDER"/>\n'
    command = [str(args.ucode), '-L', str(args.ucode_modules),
        str(ROOT / 'port/packages/tyfza1-pon-control/files/factory.uc'), str(source), str(output)]
    target = output / 'tmp/tyfza1-factory/fttr-private/ciphertext.bin'
    valid = ['00000000,01020304,89abcdef,ffffffff,12345678,80000000,00000001,a5a55a5a',
             '0,1,234,ABCDEF,80000000,FFFFFFFF,12345678,9']
    for value in valid:
        (source / 'config_hs.xml').write_bytes(hs.replace(b'PLACEHOLDER', value.encode()))
        result = subprocess.run(command, capture_output=True)
        assert result.returncode == 0, 'Factory fixture import failed'
        assert target.read_bytes() == stock.convert(value)
        assert target.stat().st_mode & 0o777 == 0o600 and target.parent.stat().st_mode & 0o777 == 0o700
    for value in ('0,1', '0,1,2,3,4,5,6,100000000', '0,1,2,3,4,5,6,invalid'):
        target.write_bytes(bytes(32))
        (source / 'config_hs.xml').write_bytes(hs.replace(b'PLACEHOLDER', value.encode()))
        result = subprocess.run(command, capture_output=True)
        assert result.returncode != 0 and not target.exists()
    for value in (b'', b'MISSING'):
        target.write_bytes(bytes(32))
        updated = hs.replace(b'PLACEHOLDER', value)
        if value: updated = updated.replace(b'HW_OLTFPGA_CIPHERTEXT', b'ABSENT')
        (source / 'config_hs.xml').write_bytes(updated)
        assert subprocess.run(command, capture_output=True).returncode == 0 and not target.exists()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('asic', 'onuinfo', 'mib', 'factory', 'ucode', 'ucode-modules', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    stock = Stock(args.asic, args.onuinfo, args.mib)
    headers = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    read_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))
    write_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint)
    delay_fn = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)
    class IO(C.Structure):
        _fields_ = [('context', C.c_void_p), ('read', read_fn), ('write', write_fn), ('delay', delay_fn)]
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        factory_cases(args, folder, stock)
        source = '''#include <errno.h>
#include <stdbool.h>
#include <string.h>
#include <sys/types.h>
#include "rtl9617c-fttr-fpga.h"
struct rtl_fttr { struct rtl_fttr_reg_io reg_io; enum rtl_fttr_backend backend; int control_lock; };
struct device { struct rtl_fttr *data; };
struct device_attribute { int unused; };
struct firmware { const unsigned char *data; unsigned int size; };
static struct firmware firmware;
static int references,locks,fw_error,requests;
#define dev_get_drvdata(d) ((d)->data)
#define sysfs_streq(a,b) (!strcmp(a,b))
#define mutex_lock(p) (locks++)
#define mutex_unlock(p) (locks--)
static int request_firmware_direct(const struct firmware **fw,const char *name,struct device *dev) {
 (void)name;(void)dev;requests++;if(fw_error)return fw_error;*fw=&firmware;references++;return 0;
}
static void release_firmware(const struct firmware *fw) { (void)fw;references--; }
'''
        source += function((headers / 'rtl9617c-fttr.c').read_text(), 'fpga_auth_store')
        source += '''
int authenticate(const struct rtl_fttr_reg_io *io,int backend,const unsigned char *data,unsigned size,int failure) {
 struct rtl_fttr fttr={.reg_io=*io,.backend=backend};struct device dev={&fttr};
 firmware=(struct firmware){data,size};references=locks=requests=0;fw_error=failure;
 int ret=fpga_auth_store(&dev,0,"1",1);
 if(references || locks)return -999;
 if(fpga_auth_store(&dev,0,"invalid",7)!=-EINVAL || references || locks)return -998;
 return ret;
}
'''
        (folder / 'native.c').write_text(source)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare',
            '-Wno-unused-parameter', '-I' + str(headers), str(folder / 'native.c'), '-o', str(folder / 'native.so')], check=True)
        native = C.CDLL(str(folder / 'native.so'))
        native.authenticate.argtypes = [C.POINTER(IO), C.c_int, C.c_char_p, C.c_uint, C.c_int]
        events, fail_at, version, result = [], 0, 0, 0
        def rd(ctx, reg, output):
            value = version if reg == 0 else result
            events.append(['read', reg, value]); output[0] = value
            return -errno.EIO if len(events) == fail_at else 0
        def wr(ctx, reg, value):
            events.append(['write', reg, value])
            return -errno.EIO if len(events) == fail_at else 0
        io = IO(None, read_fn(rd), write_fn(wr), delay_fn(lambda ctx, us: events.append(['delay', us])))
        ciphertext = bytes((i * 73 + 11) & 255 for i in range(32))
        cases = []
        for backend in (1, 2, 3):
            for version in (0x230406ff, 0x23040700, 0x23071000):
                for result in (0, 1, 0xa5a50000, 0xffffffff):
                    expected = stock.startup(backend, version, result, ciphertext)
                    events.clear()
                    ret = native.authenticate(C.byref(io), backend, ciphertext, 32, 0)
                    need = backend == 1 and version > 0x230406ff
                    assert ret == (-errno.EIO if need and result == 0xffffffff else
                        -errno.EACCES if need and result & 1 else 1)
                    prefix = [['read', 0, version]] if backend == 1 else []
                    assert events == prefix + expected, (backend, version, result, events, expected)
                    cases.append({'backend': backend, 'version': hex(version), 'status': hex(result), 'return': ret})
        version, result = 0x23040700, 0
        events.clear(); assert native.authenticate(C.byref(io), 1, ciphertext, 32, 0) == 1
        expected = list(events)
        for fail_at, operation in enumerate(expected, 1):
            if operation[0] == 'delay': continue
            events.clear(); assert native.authenticate(C.byref(io), 1, ciphertext, 32, 0) == -errno.EIO
            assert events == expected[:fail_at]
        fail_at = 0
        for size, failure, error in ((31, 0, -errno.EMSGSIZE), (32, -errno.ENOENT, -errno.ENOENT)):
            events.clear(); assert native.authenticate(C.byref(io), 1, ciphertext, size, failure) == error
            assert events == [['read', 0, version]]
        version = 0xffffffff
        events.clear(); assert native.authenticate(C.byref(io), 1, ciphertext, 32, 0) == -errno.EIO
        assert events == [['read', 0, version]]
        version = 0x230406ff
        events.clear(); assert native.authenticate(C.byref(io), 1, ciphertext, 0, -errno.ENOENT) == 1
        assert events == [['read', 0, version]]
    args.output.write_text(json.dumps({'cases': cases, 'factory_conversion': 'original libmib type13 matches',
        'stock_sha256': {n: hashlib.sha256(getattr(args, n).read_bytes()).hexdigest() for n in ('asic', 'onuinfo', 'mib')},
        'scope': 'Original startup prefix, ciphertext writes/result getter and MIB conversion; modeled registers and synthetic credentials; no FPGA hardware authentication acceptance'}, indent=2) + '\n')
    print('PASS: 36 original authentication/version cases, per-IO failures, private factory import and stale-input rejection')


if __name__ == '__main__':
    main()
