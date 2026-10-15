#!/usr/bin/env python3
"""Compare native ITU queue weights and credits with TYFZA1's PUC/DAL."""
import ctypes
from pathlib import Path
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm64_const import UC_ARM64_REG_X4

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
stock = root / 'analysis/private/live-files/lib/modules/5.10.70/extra'
base = 0x6000000
indirect = {0x8000: tuple(range(0x8014, 0x8000, -4)),
            0x8060: tuple(range(0x8074, 0x8060, -4))}
tables = {}


def callback(name, args):
    if name == 'aal_pon_mac_mode_get':
        return 3
    if name == 'memset':
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
        return args[0]
    if name in ('ca_spin_lock', 'ca_spin_unlock', 'ca_printf', 'printk',
                'aal_puc_qm_buf_internal_check'):
        return 0
    raise RuntimeError((name, args))


oracle = ModuleOracle(stock / 'ca-ne.ko',
    ['aal_puc_port_voq_map_set', 'aal_puc_port_voq_map_get.part.0',
     'aal_puc_voq_map_cfg_get', 'aal_puc_port_voq_sche_info_tbl_set'], callback)
oracle.uc.mem_map(base, 0x10000)
oracle.word(oracle.symbols['ca_venus_pon_reg_iobase'][0], base)
oracle.uc.mem_write(oracle.symbols['aal_pon_mac_mode'][0], struct.pack('<I', 3))
oracle.uc.mem_write(oracle.symbols['queue_num_per_tcont'][0], struct.pack('<I', 8))


def stock_write(uc, access, address, size, value, data):
    offset = address - base
    if offset not in indirect or not value & 0x80000000:
        return
    key = offset, value & 63
    registers = indirect[offset]
    if value & 0x40000000:
        tables[key] = [struct.unpack('<I', uc.mem_read(base + reg, 4))[0] for reg in registers]
    else:
        for reg, word in zip(registers, tables.get(key, [0] * 5)):
            uc.mem_write(base + reg, struct.pack('<I', word))


def stock_read(uc, access, address, size, value, data):
    if address - base in indirect:
        word = struct.unpack('<I', uc.mem_read(address, 4))[0]
        uc.mem_write(address, struct.pack('<I', word & 0x7fffffff))


oracle.uc.hook_add(UC_HOOK_MEM_WRITE, stock_write, begin=base, end=base + 65535)
oracle.uc.hook_add(UC_HOOK_MEM_READ, stock_read, begin=base, end=base + 65535)

# The board's DAL reads basesel, shifts the requested weight and calls VSTBL.
basesel = 0
dal_calls = []


def dal_callback(name, args):
    if name == 'aal_puc_voq_map_cfg_get':
        dal.uc.mem_write(args[1], bytes(4))
    elif name == 'dal_rt_rtl8277c_ponmisc_tcont_llid_logic2phy_get':
        dal.uc.mem_write(args[1], struct.pack('<H', args[0]))
    elif name == 'aal_puc_voq_sche_cfg_get':
        dal.uc.mem_write(args[1], struct.pack('<4I', basesel, 0, 0, 0))
    elif name == 'aal_puc_port_voq_sche_info_tbl_set':
        dal_calls.append(tuple(args[1:4]))
    elif name == 'aal_puc_port_voq_map_set':
        pointer = dal.uc.reg_read(UC_ARM64_REG_X4)
        config = struct.unpack('<I4xII', dal.uc.mem_read(pointer, 16))
        dal_calls.append((args[1], args[2], config))
    elif name not in ('aal_puc_voq_drop_set', 'aal_puc_pir_voq_tbc_mem_set',
                      'aal_puc_voq_tbc_mem_set', 'aal_puc_voq_valid_set', 'printk'):
        raise RuntimeError((name, args))
    return 0


dal = ModuleOracle(stock / 'ca-rtk.ko', ['dal_rtl8277c_ponmac_queue_add'], dal_callback,
                   external_symbols={'ca_rtk_debug': 0x5007000, 'hal_ctrl': 0x5007100})
# These are the original module's initialized-state and HAL capability inputs.
with (stock / 'ca-rtk.ko').open('rb') as stream:
    from elftools.elf.elffile import ELFFile
    elf = ELFFile(stream)
    bss = next(i for i, section in enumerate(elf.iter_sections()) if section.name == '.bss')
dal.uc.mem_write(dal.sections[bss] + 0x6a0c, struct.pack('<I', 1))
hal = dal.scratch + 0x1000
dal.word(0x5007100 + 24, hal)
dal.word(hal + 32, hal + 0x200)
dal.uc.mem_write(hal + 0x200 + 316, struct.pack('<I', 32))

read_fn = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
write_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
access_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)


class IO(ctypes.Structure):
    _fields_ = [('context', ctypes.c_void_p), ('read', read_fn),
                ('write', write_fn), ('indirect', access_fn)]


memory, native_tables = {}, {}


@read_fn
def native_read(context, reg):
    return memory.get(reg, 0)


@write_fn
def native_write(context, reg, value):
    memory[reg] = value


@access_fn
def native_access(context, reg, value):
    assert value & 0x40000000
    native_tables[reg, value & 63] = [memory.get(item, 0) for item in indirect[reg]]
    return 0


io = IO(None, native_read, native_write, native_access)
source = '#include "rtl9617c-pon-itu-puc.h"\nint configure(const struct rtl_dq_io *io, unsigned int tcont, const unsigned char *weights) { return rtl_itu_puc_schedule(io, tcont, 255, weights); }\n'
with tempfile.TemporaryDirectory(prefix='itu-schedule-', dir=root / 'artifacts') as work:
    work = Path(work)
    (work / 'native.c').write_text(source)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                    '-I' + str(drivers), str(work / 'native.c'), '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    native.configure.argtypes = [ctypes.POINTER(IO), ctypes.c_uint, ctypes.c_void_p]
    for basesel, weights in [(0, bytes([3, 5, 9, 0, 0, 0, 0, 0])),
                             (3, bytes([255, 17, 9, 4, 3, 2, 1, 7])),
                             (3, bytes(8))]:
        if any(weights):
            tables.clear()
        native_tables.clear()
        memory.clear()
        memory[0x8048] = basesel
        oracle.uc.mem_write(base, bytes(0x10000))
        for queue, weight in enumerate(weights):
            # Run the original DAL to establish the unchanged queue and credit.
            dal_calls.clear()
            dal.uc.mem_write(dal.scratch, struct.pack('<2I', 3, queue))
            dal.uc.mem_write(dal.scratch + 32, struct.pack('<5I', 0, 0, 0, weight, 0))
            assert dal.call('dal_rtl8277c_ponmac_queue_add', dal.scratch, dal.scratch + 32) == 0
            assert dal_calls == [(3, queue, (24 + queue, weight, queue)),
                                 (3, queue, weight << (7 + basesel))]
            # Execute its two original PUC setters against an indirect-table model.
            oracle.uc.mem_write(oracle.scratch, struct.pack('<IB3xII', 24 + queue, 1, weight, queue))
            assert oracle.call('aal_puc_port_voq_map_set', 0, 3, queue, 15, oracle.scratch) == 0
            assert oracle.call('aal_puc_port_voq_sche_info_tbl_set', 0, 3, queue, dal_calls[1][2]) == 0
        assert native.configure(ctypes.byref(io), 3, weights) == 0
        assert native_tables == tables, (basesel, list(weights), native_tables, tables)
        print('basesel', basesel, 'weights', list(weights), 'PVT and VSTBL match TYFZA1 DAL/AAL')
print('No optical hardware is modelled; all external calls and indirect completions are explicit.')
