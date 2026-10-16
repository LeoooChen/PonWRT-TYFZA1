#!/usr/bin/env python3
"""Compare native RF watchdog steps with actual factory ARM64 LCK/meter code."""
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'


class State(ctypes.Structure):
    _fields_ = [('thermal', ctypes.c_ubyte * 2), ('baseline', ctypes.c_ubyte * 2),
                ('periods', ctypes.c_uint), ('runs', ctypes.c_uint),
                ('timeouts', ctypes.c_uint), ('result', ctypes.c_int)]


class Bus:
    def __init__(self, thermal, busy=0, fail_channel=False):
        self.reg = {(0, 0): 0x52345, (1, 0): 0x61234, (0, 5): 0x12345,
                    (1, 5): 0x23456, (0, 0x18): 0x10124, (0, 0xd3): 0x35,
                    (0, 0x42): thermal[0] << 1, (1, 0x42): thermal[1] << 1}
        self.busy, self.fail_channel, self.trace = busy, fail_channel, []

    def read(self, path, address):
        self.trace.append(['read', path, address])
        if address == 0xb7:
            busy = self.busy > 0
            self.busy -= busy
            return 0x40 | (0x100 if busy else 0)
        return self.reg.get((path, address), 0)

    def write(self, path, address, mask, value):
        self.trace.append(['write', path, address, mask, value])
        if self.fail_channel and address == 0x18:
            self.fail_channel = False
            return -5
        shift = (mask & -mask).bit_length() - 1
        before = self.reg.get((path, address), 0)
        self.reg[path, address] = (before & ~mask) | ((value << shift) & mask)
        return 0

    def delay(self, us):
        self.trace.append(['delay', us])


def callback(name, args):
    if name == 'rtw_hal_read_rf_reg':
        _, path, address, mask = args
        return (stock.read(path, address) & mask) >> ((mask & -mask).bit_length() - 1)
    if name == 'halrf_wrf':
        _, path, address, mask = args
        return stock.write(path, address, mask, oracle.uc.reg_read(UC_ARM64_REG_X4))
    if name == 'halrf_delay_us':
        stock.delay(args[1])
        return 0
    assert name == 'printk', (name, args)
    return 0


names = ['halrf_get_thermal', 'halrf_get_thermal_8852c', 'halrf_get_thermal_8192xb',
         'halrf_lck_tracking', 'halrf_lck_tracking_8852c', 'halrf_lck_tracking_8192xb',
         'halrf_lck_trigger', 'halrf_lck_8852c', 'halrf_lck_8192xb',
         'halrf_set_ch_lck_8852c', 'halrf_set_ch_lck_8192xb']
oracle = ModuleOracle(module, names, callback)
rf, hal = 0x6000000, 0x6010000
oracle.uc.mem_map(rf, 0x20000)

read_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint))
write_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)
delay_type = ctypes.CFUNCTYPE(None, ctypes.c_uint)


@read_type
def native_read(path, address, value):
    value[0] = native_bus.read(path, address)
    return 0


@write_type
def native_write(path, address, mask, value):
    return native_bus.write(path, address, mask, value)


@delay_type
def native_delay(us):
    native_bus.delay(us)


source = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8; typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define max(a,b) ((a) > (b) ? (a) : (b))
#define RTL_AX_AP_PHY_H
#include "power.h"
static int (*rf_read)(unsigned int, unsigned int, u32 *);
static int (*rf_write)(unsigned int, unsigned int, u32, u32);
static void (*rf_delay)(unsigned int);
int rtl_ax_phy_rf_read(const struct rtl_ax_io *io, unsigned int path, unsigned int reg, u32 *v)
{ return rf_read(path, reg, v); }
int rtl_ax_phy_rf_write_masked(const struct rtl_ax_io *io, unsigned int path, unsigned int reg, u32 mask, u32 v)
{ return rf_write(path, reg, mask, v); }
static void delay(void *ctx, unsigned int us) { rf_delay(us); }
#include "rf-runtime.h"
void bind(void *r, void *w, void *d) { rf_read=r; rf_write=w; rf_delay=d; }
int update(unsigned int chip, struct rtl_ax_rf_runtime *state) {
    struct rtl_ax_io io = {.delay_us=delay};
    return rtl_ax_rf_runtime_update(&io, chip, state);
}
'''
rows = []
with tempfile.TemporaryDirectory(prefix='tyfza1-rf-runtime-') as tmp:
    c, so = Path(tmp) / 'native.c', Path(tmp) / 'native.so'
    c.write_text(source)
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Werror', '-I', str(driver),
                    str(c), '-o', str(so)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.bind.argtypes = [read_type, write_type, delay_type]
    lib.bind(native_read, native_write, native_delay)
    lib.update.argtypes = [ctypes.c_uint, ctypes.POINTER(State)]
    for chip, native_chip, original_chip in [('8852c', 0x52, 8), ('8192xb', 0x53, 32)]:
        for case, thermal, baseline, busy in [
            ('first', (31, 34), (0, 0), 3),
            ('delta7', (38, 27), (31, 34), 0),
            ('pathA_delta8', (39, 34), (31, 34), 0),
            ('pathB_cools8', (31, 26), (31, 34), 0),
            ('timeout', (39, 34), (31, 34), 1000),
        ]:
            stock = Bus(thermal, busy)
            oracle.uc.mem_write(rf, bytes(0x20000))
            oracle.word(rf + 8, hal)
            oracle.uc.mem_write(rf + 0x10, struct.pack('<I', original_chip))
            oracle.uc.mem_write(rf + 0x24, struct.pack('<I', 4))
            oracle.uc.mem_write(rf + 0x4d, bytes(baseline))
            for path in (0, 1):
                value = oracle.call('halrf_get_thermal', rf, path)
                oracle.uc.mem_write(rf + 0x4b + path, bytes([value]))
            oracle.call('halrf_lck_tracking', rf, instruction_budget=1000000)
            stock_baseline = bytes(oracle.uc.mem_read(rf + 0x4d, 2))
            native_bus = Bus(thermal, busy)
            state = State()
            state.baseline[:] = baseline
            result = lib.update(native_chip, ctypes.byref(state))
            assert native_bus.trace == stock.trace, (chip, case, native_bus.trace, stock.trace)
            assert native_bus.reg == stock.reg, (chip, case)
            assert tuple(state.thermal) == thermal
            if case == 'timeout':
                assert result == -110 and state.timeouts == 1 and state.runs == 0
                assert tuple(state.baseline) == baseline and stock_baseline == bytes(thermal)
            else:
                assert result == 0 and bytes(state.baseline) == stock_baseline
                assert state.runs == (case != 'delta7')
            rows.append(dict(chip=chip, case=case, result=result,
                             matched_RF_operations=len(stock.trace),
                             trace_sha256=hashlib.sha256(json.dumps(stock.trace).encode()).hexdigest(),
                             native_baseline=list(state.baseline), stock_baseline=list(stock_baseline)))
    native_bus = Bus((39, 34), fail_channel=True)
    state = State()
    state.baseline[:] = (31, 34)
    assert lib.update(0x53, ctypes.byref(state)) == -5
    assert tuple(state.baseline) == (31, 34) and state.runs == 0
    assert not (native_bus.reg[0, 0xd3] & 0x100)
    assert (native_bus.reg[0, 0], native_bus.reg[1, 0], native_bus.reg[0, 5], native_bus.reg[1, 5]) == (0x52345, 0x61234, 0x12345, 0x23456)

print(json.dumps(dict(factory_module_sha256=digest,
    native_header_sha256=hashlib.sha256((driver / 'rf-runtime.h').read_bytes()).hexdigest(),
    cases=rows, RF_IO_error_restores_operational_state=True,
    scope='Actual factory ARM64 functions versus compiled production native header. '
          'RF register IO and delays are modeled. Factory poll timeout is deliberately '
          'changed to return ETIMEDOUT and retain the last successful baseline. '
          'No RF calibration, IRQ, Linux scheduling or hardware throughput is measured.'), indent=2))
