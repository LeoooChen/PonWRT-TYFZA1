#!/usr/bin/env python3
"""Replay this board's XGTC business T-CONT add/delete, not loss-sync recovery."""
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
modules = root / 'analysis/private/live-files/lib/modules/5.10.70/extra'
base = 0x6000000
state = {}


def aal_callback(name, args):
    if name in ('ca_spin_lock', 'ca_spin_unlock', 'ca_printf', 'printk'):
        return 0
    raise RuntimeError((name, args))


aal = ModuleOracle(modules / 'ca-ne.ko', ['aal_xgpon_bwmp_tcont_set'], aal_callback)
aal.uc.mem_map(base, 0x10000)
aal.word(aal.symbols['ca_venus_pon_reg_iobase'][0], base)


def stock_read(uc, access, address, size, value, data):
    if address == base + 0x4838:
        value = aal.unpack(address, 'I')[0]
        uc.mem_write(address, struct.pack('<I', value & ~(1 << 31)))


def stock_write(uc, access, address, size, value, data):
    state['writes'].append([address - base, value])
    if address == base + 0x4838:
        if value & (1 << 30):
            state['table'] = aal.unpack(base + 0x483c, 'I')[0]
        else:
            uc.mem_write(base + 0x483c, struct.pack('<I', state['table']))


aal.uc.hook_add(UC_HOOK_MEM_READ, stock_read, begin=base, end=base + 0xffff)
aal.uc.hook_add(UC_HOOK_MEM_WRITE, stock_write, begin=base, end=base + 0xffff)


def dal_callback(name, args):
    if name == 'ca_mdelay':
        state['delays'].append(args[0])
        return 0
    if name != 'aal_xgpon_bwmp_tcont_set':
        raise RuntimeError((name, args))
    # ModuleOracle exposes four arguments; the AAL config pointer is the fifth.
    from unicorn.arm64_const import UC_ARM64_REG_X4
    config = bytes(dal.uc.mem_read(dal.uc.reg_read(UC_ARM64_REG_X4), 12))
    state['calls'].append(dict(arguments=args, config=list(struct.unpack('<III', config))))
    aal.uc.mem_write(aal.scratch, config)
    return aal.call(name, *args, aal.scratch)


dal = ModuleOracle(modules / 'ca-rtk.ko',
                   ['dal_rt_rtl8277c_gpon_tcont_set', 'dal_rt_rtl8277c_gpon_tcont_del'],
                   dal_callback, external_symbols={'ca_rtk_debug': base + 0x20000})
dal.uc.mem_map(base, 0x30000)
dal.uc.mem_write(dal.symbols['gpon_init'][0], struct.pack('<I', 1))
dal.word(dal.symbols['g_gpon_ploam_rx'][0] + 72, base)
records = []
for mode in (4, 5):
    dal.uc.mem_write(dal.symbols['CA_PON_MODE'][0], bytes([mode]))
    for ploam in (0, 1):
        for index in range(31):
            dal.uc.mem_write(base + index * 8, struct.pack('<II', index + 1, 65535))
        state.update(table=0xa0003aad, writes=[], calls=[], delays=[])
        assert dal.call('dal_rt_rtl8277c_gpon_tcont_set', base + 0x10000, 1063) == 0
        assert state['table'] == 0xb1000427
        assert state['calls'] == [dict(arguments=[0, 0, 1, 7], config=[1063, 1, 1])]
        initial = 0xa1000427 | (ploam << 28)
        state.update(table=initial, writes=[], calls=[], delays=[])
        assert dal.call('dal_rt_rtl8277c_gpon_tcont_del', 0) == 0
        assert state['delays'] == [1]
        assert state['calls'] == [dict(arguments=[0, 0, 1, 3], config=[0x3aad, 0, 0])]
        assert state['table'] == (initial if ploam else 0xa0003aad)
        assert all(address != 0x4004 for address, _ in state['writes'])
        records.append(dict(mode=mode, ploam=ploam, initial=initial,
                            released=state['table'], delay_ms=state['delays'],
                            calls=state['calls'], mmio_writes=state['writes']))

read_type = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
write_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
access_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)


class IO(ctypes.Structure):
    _fields_ = [('context', ctypes.c_void_p), ('read', read_type),
                ('write', write_type), ('indirect', access_type)]


@read_type
def read(context, address):
    return state['memory'][address]


@write_type
def write(context, address, value):
    state['native_writes'].append([address, value])
    state['memory'][address] = value


@access_type
def indirect(context, address, command):
    write(context, address, command)
    if state['error']:
        return state['error']
    if command & (1 << 30):
        state['table'] = state['memory'][0x483c]
    else:
        state['memory'][0x483c] = state['table']
    return 0


io = IO(None, read, write, indirect)
with tempfile.TemporaryDirectory(prefix='xgpon-business-') as directory:
    work = Path(directory)
    (work / 'native.c').write_text('''
#include "rtl9617c-xgpon-hw.h"
int release(const struct rtl_dq_io *io) { return rtl_xgpon_tcont_release(io, 1); }
''')
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                    '-I', str(drivers), str(work / 'native.c'), '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    native.release.argtypes = [ctypes.POINTER(IO)]
    for record in records:
        state.update(table=record['initial'], memory={}, native_writes=[], error=0)
        assert native.release(ctypes.byref(io)) == 0
        assert state['table'] == record['released']
        assert state['native_writes'] == record['mmio_writes']
    state['error'] = -110
    assert native.release(ctypes.byref(io)) == -110

result = dict(module_sha256={name: hashlib.sha256((modules / name).read_bytes()).hexdigest()
                            for name in ('ca-ne.ko', 'ca-rtk.ko')}, records=records,
              scope='Actual stock DAL and AAL instructions versus native release helper. '
                    'Indirect completion and locks modeled; no optical or FIFO timing proof.')
(root / 'logs/stock-xgpon-business.json').write_text(json.dumps(result, indent=2) + '\n')
print('XG-PON/XGS-PON business release: stock/native MMIO match, no global flush, PLOAM ownership retained; access timeout propagates')
