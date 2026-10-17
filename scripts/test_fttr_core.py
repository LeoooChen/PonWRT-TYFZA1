#!/usr/bin/env python3
"""Compare ASIC core/DBA writes and the original caller's mode-specific PCS checks."""
import argparse
import ctypes
import errno
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_SP, UC_ARM64_REG_PC, UC_ARM64_REG_LR

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_elf_oracle import ElfOracle


class Registers:
    def __init__(self, seed=0, pcs=0, fail_reg=None):
        self.seed, self.pcs, self.fail_reg = seed, pcs, fail_reg
        self.values, self.events = {}, []

    def read(self, reg):
        value = self.values.get(reg, self.seed)
        if reg == 0xc09000 and self.values.get(0xc09004) == 2:
            value = value & 0xffff0000 | self.pcs
        self.events.append(['read', reg, value])
        return value

    def write(self, reg, value):
        self.values[reg] = value
        self.events.append(['write', reg, value])


def stock(binary, registers):
    def handler(name, args):
        if name == 'FPGA_reg_read':
            return registers.read(args[0])
        if name == 'FPGA_reg_write':
            registers.write(*args[:2])
        elif name == 'rtk_switch_version_get':
            for pointer, value in zip(args, (0x8277c000, 67, 0)):
                oracle.uc.mem_write(pointer, struct.pack('<I', value))
        elif name in ('drv_get_miniolt_chiptype', 'open'):
            return 3
        elif name not in ('flock', 'close'):
            raise AssertionError(('unmodeled original call', name))
        return 0

    oracle = ElfOracle(binary, handler, ('FPGA_reg_read', 'FPGA_reg_write', 'drv_get_miniolt_chiptype'))
    return oracle


def caller_link(binary, mode, pcs):
    """Execute onuinfo 41906c..419324 with explicit mode and status inputs only."""
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    with binary.open('rb') as stream:
        elf = ELFFile(stream)
        segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        first = min(s['p_vaddr'] for s in segments) & ~4095
        last = (max(s['p_vaddr'] + s['p_memsz'] for s in segments) + 4095) & ~4095
        uc.mem_map(first, last - first)
        for segment in segments:
            uc.mem_write(segment['p_vaddr'], segment.data())
    output = subprocess.check_output(['aarch64-linux-gnu-objdump', '-d', str(binary)], text=True)
    hooks = {int(address, 16): name for address, name in
             re.findall(r'^([0-9a-f]+) <([^<>]+)@plt>:', output, re.M)}
    stack = 0x20000000
    uc.mem_map(stack, 0x10000)
    uc.reg_write(UC_ARM64_REG_SP, stack)
    uc.mem_write(stack + 167, bytes([mode]))
    queries = []

    class NotReady(Exception):
        pass

    def hook(uc, address, size, unused):
        name = hooks.get(address)
        if name is None:
            return
        if name in ('WTG_SerdesIndirectRead', 'SdsPort6RegRead'):
            queries.append([name, uc.reg_read(UC_ARM64_REG_X0), uc.reg_read(UC_ARM64_REG_X1)])
            value = pcs
        elif name == 'WTG_serdes_init':
            raise NotReady()
        elif name in ('printf', 'puts'):
            value = 0
        else:
            raise AssertionError(('unmodeled caller function', name))
        uc.reg_write(UC_ARM64_REG_X0, value)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    uc.hook_add(UC_HOOK_CODE, hook)
    try:
        uc.emu_start(0x41906c, 0x419324, count=20000)
    except NotReady:
        return False, queries
    assert uc.reg_read(UC_ARM64_REG_PC) == 0x419324
    return True, queries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', required=True, type=Path)
    parser.add_argument('--caller', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    read_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint))
    write_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
    delay_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint)

    class IO(ctypes.Structure):
        _fields_ = [('context', ctypes.c_void_p), ('read', read_fn), ('write', write_fn), ('delay', delay_fn)]

    cases = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'native.c').write_text('''#include <stdbool.h>
#include <errno.h>
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U >> (31-(h))) & (~0U << (l)))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#include "rtl9617c-fttr-core.h"
#include "rtl9617c-fttr-serdes.h"
int core(const struct rtl_fttr_reg_io *io) {return rtl_fttr_core_init(io);}
int dba(const struct rtl_fttr_reg_io *io) {return rtl_fttr_dba_defaults(io);}
int tcont(const struct rtl_fttr_reg_io *io,unsigned int id,unsigned int tc,unsigned int f,unsigned int a,unsigned int m) {
 return rtl_fttr_tcont_program(io,id,tc,f,a,m);
}
int link(const struct rtl_fttr_reg_io *io,unsigned int mode,unsigned int *ready) {
 return rtl_fttr_wtg_link(io,mode,ready);
}
int management(const struct rtl_fttr_reg_io *io,unsigned int id,int enable) {
 return rtl_fttr_management_grant(io,id,enable);
}
''')
        headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-I', str(headers), str(path / 'native.c'), '-o', str(path / 'native.so')], check=True)
        library = ctypes.CDLL(str(path / 'native.so'))

        def native_io(registers):
            def rd(context, reg, pointer):
                pointer[0] = registers.read(reg)
                return -errno.EIO if reg == registers.fail_reg else 0

            def wr(context, reg, value):
                registers.write(reg, value)
                return -errno.EIO if reg == registers.fail_reg else 0

            return IO(None, read_fn(rd), write_fn(wr), delay_fn(lambda *_: None))

        def writes(registers):
            return [e for e in registers.events if e[0] == 'write']

        for seed in (0, 0xffffffff):
            expected, actual = Registers(seed), Registers(seed)
            oracle = stock(args.stock, expected)
            assert oracle.call('UMM1001M_regInit') == 0
            io = native_io(actual)
            assert library.core(ctypes.byref(io)) == 0
            additional = [e for e in writes(actual) if 0x41100 <= e[1] <= 0x417ff]
            assert additional == [['write',reg,0] for reg in range(0x41100,0x41800)]
            assert [e for e in actual.events if not 0x41100 <= e[1] <= 0x417ff] == expected.events
            cases.append({'operation': 'core_init', 'seed': hex(seed), 'writes': len(writes(actual)),
                          'original_io_match': True, 'additional_dba_clear_words': len(additional)})
            expected.events.clear()
            actual.events.clear()
            for name, value in [('bw_max',19440),('bw_min_user_fec',38),('bw_min_omci_fec',38),
                                ('bw_min_user',10),('bw_min_omci',10)]:
                oracle.call('drv_set_olt_dba_' + name, value)
            assert library.dba(ctypes.byref(io)) == 0
            assert actual.events == expected.events
        for id, tc, fixed, assured, maximum in [(0,0,0,10,10),(5,3,50,100,200),(63,7,0,0,19440)]:
            expected, actual = Registers(), Registers()
            oracle = stock(args.stock, expected)
            data = oracle.scratch + 0x2000
            oracle.uc.mem_write(data, struct.pack('<HHHHH', 0, 0, fixed, assured, maximum))
            assert oracle.call('drv_set_onu_tcont_dba_profile', id + 1, tc, data) == 0
            io = native_io(actual)
            assert library.tcont(ctypes.byref(io), id, tc, fixed, assured, maximum) == 0
            assert actual.events == expected.events
            cases.append({'operation':'tcont','id':id,'slot':tc,'stock_native_match':True})
        for id in (0,5,63):
            expected, actual = Registers(), Registers()
            expected.values.update({0x40020:0x26260a0a,0x40021:19440,0x40002:0x22})
            oracle = stock(args.stock, expected)
            base = oracle.symbols['OltSetting']
            oracle.uc.mem_write(base,struct.pack('<IIIIIII',19440,10,10,38,38,0,60))
            for slot in range(64):
                oracle.uc.mem_write(base+slot*136+41,b'\x02')
                for tc in range(8):
                    oracle.uc.mem_write(base+slot*136+80+tc*12,struct.pack('<III',0,
                        3840 if tc==0 else 0,3840 if tc==0 else 19440*64))
            assert oracle.call('SetOnuTcontDba',id+1,0,0,3840,3840)==0
            io=native_io(actual)
            assert library.management(ctypes.byref(io),id,1)==0
            assert writes(actual)==writes(expected)
            actual.events.clear()
            assert library.management(ctypes.byref(io),id,0)==0
            assert actual.events==[['write',0x41000+id*32,0],['write',0x41001+id*32,0]]
            cases.append({'operation':'management_grant','id':id,'original_callback_writes_match':True})
        for mode, page, reg, bit, wrong in [(5,1,30,4,12),(9,5,0,12,4)]:
            for pcs, ready in [(1 << bit,True),(1 << wrong,False)]:
                result, queries = caller_link(args.caller, mode, pcs)
                assert result == ready
                order = ('WTG_SerdesIndirectRead','SdsPort6RegRead') * 2 if mode == 5 else (
                    'WTG_SerdesIndirectRead','WTG_SerdesIndirectRead','SdsPort6RegRead','SdsPort6RegRead')
                assert queries[:4] == [[n,page,reg] for n in order], (mode, pcs, queries)
                expected, actual = Registers(pcs=pcs), Registers(pcs=pcs)
                oracle = stock(args.stock, expected)
                oracle.call('WTG_SerdesIndirectRead', page, reg)
                assert bool(oracle.call('WTG_SerdesIndirectRead', page, reg) & (1 << bit)) == ready
                io, value = native_io(actual), ctypes.c_uint(0)
                assert library.link(ctypes.byref(io), mode, ctypes.byref(value)) == 0
                assert bool(value.value) == ready and actual.events == expected.events
            cases.append({'operation':'pcs_link','mode':mode,'page':page,'reg':reg,'bit':bit,
                          'original_caller_branch_verified':True})
        broken = Registers(fail_reg=0x42000)
        io = native_io(broken)
        assert library.core(ctypes.byref(io)) == -errno.EIO
        assert broken.events[-1][:2] == ['write',0x42000]
        broken = Registers(fail_reg=0x41001)
        io = native_io(broken)
        assert library.tcont(ctypes.byref(io),0,0,1,2,3) == -errno.EIO
        assert broken.events[-1][:2] == ['write',0x41001]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256':hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'caller_sha256':hashlib.sha256(args.caller.read_bytes()).hexdigest(),'cases':cases,
        'register_errors_stop':True,'hardware_verified':False},indent=2)+'\n')
    print('Core/DBA: original writes match plus complete DBA clear; mode-specific PCS and transport failures verified')


if __name__ == '__main__':
    main()
