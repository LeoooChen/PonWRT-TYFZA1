#!/usr/bin/env python3
"""Compare the native report configuration with the board's set_host_rpr."""
from pathlib import Path
import ctypes
import json
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_module_oracle import ModuleOracle

module = ROOT / 'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko'
registers, writes = {}, []


def callback(name, args):
    if name == 'is_qta_poh':
        oracle.uc.mem_write(args[2], b'\x01')
        return 0
    if name == 'report_read32':
        return registers[args[1]]
    if name == 'report_write32':
        registers[args[1]] = args[2] & 0xffffffff
        writes.append((args[1], args[2] & 0xffffffff))
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['set_host_rpr'], callback)
adapter, ops, intf, env, cfg = [0x06000000 + n * 0x1000 for n in range(5)]
oracle.uc.mem_map(adapter, 0x10000)
oracle.word(adapter, ops)
oracle.word(ops, intf)
oracle.word(adapter + 0x58, env)
oracle.uc.mem_write(env + 8, struct.pack('<I', 2))
oracle.word(intf + 32, oracle.callback('report_read32'))
oracle.word(intf + 40, oracle.callback('report_write32'))
results = []
for aggregate in (121, 30):
    registers = {0x9408: 0x4003, 0x9410: 0x00070001, 0x9414: 0xa5125a08}
    writes = []
    oracle.uc.mem_write(cfg, struct.pack('<BBBB4I', aggregate, 255, 0, 0, 1, 1, 1, 1))
    assert oracle.call('set_host_rpr', adapter, cfg) == 0
    assert registers == {0x9408: 0x4000, 0x9410: 0x0f070001, 0x9414: 0xa5ff5a00 | aggregate}
    count = len(writes)
    assert oracle.call('set_host_rpr', adapter, cfg) == 0 and len(writes) == count
    results.append(dict(aggregate=aggregate, register_9414=hex(registers[0x9414]),
                        writes=writes, repeated_call_no_write=True))

native = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
out = ROOT / 'artifacts/report-credit63'
out.mkdir(exist_ok=True)
(out / 'config.c').write_text(r'''
#include <stdbool.h>
#include <stdint.h>
typedef uint32_t u32;
#include "mac-runtime.h"
static unsigned int config;
static unsigned int read32(void *ctx, unsigned int reg)
{ (void)ctx; return reg == 0x9414 ? config : 0; }
static void write32(void *ctx, unsigned int reg, unsigned int value)
{ (void)ctx; if (reg == 0x9414) config = value; }
unsigned int native_config(unsigned int initial)
{
    struct rtl_ax_io io = {.read32 = read32, .write32 = write32};
    config = initial;
    rtl_ax_mac_trx_registers(&io);
    return config;
}
''')
subprocess.run(['cc', '-O2', '-shared', '-fPIC', '-I', str(native), str(out / 'config.c'),
                '-o', str(out / 'config.so')], check=True)
library = ctypes.CDLL(str(out / 'config.so'))
library.native_config.restype = ctypes.c_uint
native_result = library.native_config(0xa5125a08)
assert native_result == int(results[1]['register_9414'], 16)
print(json.dumps(dict(scope='Actual stock AArch64 set_host_rpr with explicit MMIO callbacks; native init compiled from its header. Two report sizes, not a radio test.',
                      stock=results, native_register_9414=hex(native_result)), indent=2))
