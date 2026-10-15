#!/usr/bin/env python3
"""Compare GPON table readback ownership with this board's original AAL."""
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
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
names = ['aal_gpon_tcont_set', 'aal_gpon_ds_gem_port_set']
base = 0x6000000


def callback(name, args):
    if name in ('ca_spin_lock', 'ca_spin_unlock', 'ca_printf', 'printk'):
        return 0
    raise RuntimeError((name, args))


oracle = ModuleOracle(module, names, callback)
oracle.uc.mem_map(base, 0x10000)
oracle.word(oracle.symbols['ca_venus_pon_reg_iobase'][0], base)
state = {}


def access(command, load, fetch):
    if command & (1 << 30):
        requested = fetch()
        state['table'] = requested if command & (1 << 16) else (
            requested & ~state['hardware_bit']) | (state['table'] & state['hardware_bit'])
        state['table'] ^= state['corrupt']
    else:
        load(state['table'])


def stock_write(uc, kind, address, size, value, data):
    state['stock_writes'].append((address - base, value))
    if address == base + state['command']:
        access(value, lambda v: uc.mem_write(base + state['data'], struct.pack('<I', v)),
               lambda: oracle.unpack(base + state['data'], 'I')[0])


def stock_read(uc, kind, address, size, value, data):
    if address == base + state['command']:
        word = oracle.unpack(address, 'I')[0]
        uc.mem_write(address, struct.pack('<I', word & ~(1 << 31)))


oracle.uc.hook_add(UC_HOOK_MEM_WRITE, stock_write, begin=base, end=base + 0xffff)
oracle.uc.hook_add(UC_HOOK_MEM_READ, stock_read, begin=base, end=base + 0xffff)
read_fn = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
write_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
indirect_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)


class IO(ctypes.Structure):
    _fields_ = [('context', ctypes.c_void_p), ('read', read_fn),
                ('write', write_fn), ('indirect', indirect_fn)]


@read_fn
def read(context, address):
    return state['memory'].get(address, 0)


@write_fn
def write(context, address, value):
    state['native_writes'].append((address, value))
    state['memory'][address] = value


@indirect_fn
def indirect(context, address, command):
    write(context, address, command)
    access(command, lambda v: state['memory'].__setitem__(state['data'], v),
           lambda: state['memory'][state['data']])
    return 0


io = IO(None, read, write, indirect)
source = '''
#include "rtl9617c-gpon-hw.h"
int tcont(const struct rtl_dq_io *io, unsigned int software)
{ return rtl_gpon_tcont_set(io, 0x123, 1, 0, 1, software); }
int gem(const struct rtl_dq_io *io, unsigned int software)
{ return rtl_gpon_downstream_gem_set(io, 0x123, 8, 1, 0, software); }
'''
records = []
with tempfile.TemporaryDirectory(prefix='gpon-ownership-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    (work / 'native.c').write_text(source)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                    '-I', str(drivers), str(work / 'native.c'), '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    for kind, command, data, hardware_bit, mask, config in (
        ('tcont', 0x612c, 0x6130, 1, 7, struct.pack('<H2xII', 1, 1, 0)),
        ('gem', 0x6134, 0x6138, 2, 13, struct.pack('<H2xIII', 8, 0, 0, 1)),
    ):
        function = getattr(native, kind)
        function.argtypes = [ctypes.POINTER(IO), ctypes.c_uint]
        for software in (0, 1):
            for hardware_value in (0, hardware_bit):
                initial = 0xa5000000 | hardware_value
                state.update(command=command, data=data, hardware_bit=hardware_bit,
                             corrupt=0, table=initial, memory={}, stock_writes=[], native_writes=[])
                oracle.uc.mem_write(oracle.scratch, config)
                stock = oracle.call(names[kind == 'gem'], 0, software, 0x123, mask, oracle.scratch)
                stock_table = state['table']
                state['table'] = initial
                result = function(ctypes.byref(io), software)
                records.append(dict(kind=kind, software=software, hardware_value=hardware_value,
                                    stock_result=stock, native_result=result, table=state['table']))
                assert stock == 0, records[-1]
                assert state['table'] == stock_table
                assert state['native_writes'] == state['stock_writes']
        # A failed software-owned field must still fail. Also verify the
        # PLOAM/AES bit when the caller explicitly requests software control.
        for software, corrupt in ((0, 1 << 3), (1, hardware_bit)):
            state.update(corrupt=corrupt, table=0, memory={}, native_writes=[])
            assert function(ctypes.byref(io), software) == -5

result = dict(factory_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
              actual_functions=names, records=records,
              scope='Actual original AAL and native helpers; modeled indirect completion and GPON bit16 write ownership. No silicon or optical validation.')
(root / 'logs/stock-gpon-table-ownership.json').write_text(json.dumps(result, indent=2) + '\n')
assert all(record['native_result'] == 0 for record in records), records
print('GPON table ownership:8 original/native comparisons passed; software-owned readback failures still return EIO')
