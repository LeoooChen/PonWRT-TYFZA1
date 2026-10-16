#!/usr/bin/env python3
"""Compare the shipped OMCC AES interrupt with the native table update."""
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
base = 0x6000000
records = []
state = {}


def callback(name, args):
    if name in ('ca_printf', 'ca_spin_lock', 'ca_spin_unlock', 'printk'):
        return 0
    raise RuntimeError((name, args))


oracle = ModuleOracle(module, ['__pon_misc_isr', 'aal_xgpon_usbg_xgem_cfg_get_isr',
                              'aal_xgpon_usbg_xgem_cfg_set_isr',
                              'aal_xgpon_usbg_glb_ctrl_set_isr'], callback)
oracle.uc.mem_map(base, 0x10000)
oracle.word(oracle.symbols['ca_venus_pon_reg_iobase'][0], base)


def stock_read(uc, access, address, size, value, data):
    if address == base + 0x4618:
        uc.mem_write(address, struct.pack('<I', oracle.unpack(address, 'I')[0] & ~(1 << 31)))


def stock_write(uc, access, address, size, value, data):
    if address == base + 0x4618:
        index = value & 255
        if value & (1 << 30):
            state['table'][index] = oracle.unpack(base + 0x461c, 'I')[0]
        else:
            uc.mem_write(base + 0x461c, struct.pack('<I', state['table'][index]))


oracle.uc.hook_add(UC_HOOK_MEM_READ, stock_read, begin=base, end=base + 0xffff)
oracle.uc.hook_add(UC_HOOK_MEM_WRITE, stock_write, begin=base, end=base + 0xffff)
read_type = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
write_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
access_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)


class IO(ctypes.Structure):
    _fields_ = [('context', ctypes.c_void_p), ('read', read_type),
                ('write', write_type), ('indirect', access_type)]


@read_type
def read(context, address):
    return state['native_regs'].get(address, 0)


@write_type
def write(context, address, value):
    state['native_regs'][address] = value


@access_type
def indirect(context, address, command):
    assert address == 0x4618
    state['accesses'] += 1
    if state['accesses'] == state['fail_access']:
        return -110
    index = command & 255
    if command & (1 << 30):
        state['native_table'][index] = state['native_regs'][0x461c]
    else:
        state['native_regs'][0x461c] = state['native_table'][index]
    return 0


io = IO(None, read, write, indirect)
with tempfile.TemporaryDirectory(prefix='xgpon-omcc-aes-') as temp:
    c, lib = Path(temp) / 'check.c', Path(temp) / 'check.so'
    c.write_text('#include "rtl9617c-xgpon-hw.h"\n'
                 'int toggle(const struct rtl_dq_io *io){return rtl_xgpon_omcc_aes_toggle(io);}\n')
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                    '-I', str(drivers), str(c), '-o', str(lib)], check=True)
    native = ctypes.CDLL(str(lib))
    native.toggle.argtypes = [ctypes.POINTER(IO)]
    for encrypted in (0, 1):
        for business in (0, 1):
            initial = [0xa0000000] * 256
            for i in range(8):
                initial[i] |= 17 | (1 << 16) | (encrypted << 17)
            initial[8] |= 4000 | (business << 16) | (business << 17)
            state.update(table=initial.copy(), native_table=initial.copy(), accesses=0,
                         fail_access=0, native_regs={0x4600: 0x40000c0})
            oracle.uc.mem_write(base + 0x4600, struct.pack('<I', 0x40000c0))
            oracle.uc.mem_write(oracle.symbols['omcc_aes_mode'][0], struct.pack('<I', encrypted))
            configs = b''.join(struct.pack('<IIII', word & 65535, (word >> 16) & 1,
                                          (word >> 17) & 1, (word >> 18) & 1) for word in initial)
            oracle.uc.mem_write(oracle.symbols['aes_usbg_xgem_cfg'][0], configs)
            assert oracle.call('__pon_misc_isr', 7, 1 << 5) == 0
            assert native.toggle(ctypes.byref(io)) == 0
            assert state['native_table'] == state['table']
            assert state['native_regs'][0x4600] == oracle.unpack(base + 0x4600, 'I')[0]
            assert state['table'][8:] == initial[8:]
            records.append(dict(initial_omcc_aes=encrypted, business_encrypted=business,
                                resulting_omcc_aes=1 - encrypted,
                                usbg_control=state['native_regs'][0x4600]))
    # The original AAL logs a busy timeout but returns success. Retain the
    # native transport's real error instead of copying that behavior.
    state.update(native_table=initial.copy(), native_regs={}, accesses=0, fail_access=1)
    assert native.toggle(ctypes.byref(io)) == -110
    assert state['native_table'] == initial

result = dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(), records=records,
              scope='Actual shipped misc ISR and USBG getter/setters; table completion modeled. '
                    'Compare resulting table/control state, not identical bus transaction count. '
                    'Does not prove encrypted OMCI reception by a physical OLT.',
              native_first_read_timeout=-110)
(root / 'logs/stock-xgpon-omcc-aes.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
