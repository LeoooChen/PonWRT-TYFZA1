#!/usr/bin/env python3
"""Compare production physical MIB reads with the board's actual ARM64 AAL."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--stock", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
drivers = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
base = 0x6000000
state = {}


def value_for(reg, command):
    index = command & 31
    port = command >> 5 & 7
    value = 0x1000 + port * 0x100 + index
    if reg in (0xa1b0, 0x630):
        value += 0x10000
    if index == 25:
        value |= 3 << 32
    return value


def callback(name, values):
    if name in ("ca_spin_lock", "ca_spin_unlock", "ca_printf", "printk"):
        return 0
    if name == "memset":
        oracle.uc.mem_write(values[0], bytes([values[1] & 255]) * values[2])
        return values[0]
    raise RuntimeError((name, values))


def stock_write(uc, access, address, size, value, context):
    reg = address - base
    state["stock_trace"].append((reg, value))
    if reg in (0xa1a4, 0xa1b0):
        data = value_for(reg, value)
        uc.mem_write(address + 4, struct.pack("<II", data >> 32, data & 0xffffffff))
    elif reg in (0x618, 0x630):
        uc.mem_write(address + 4, struct.pack("<I", value_for(reg, value)))


def stock_read(uc, access, address, size, value, context):
    if address - base in (0xa1a4, 0xa1b0, 0x618, 0x630):
        command = struct.unpack("<I", uc.mem_read(address, 4))[0]
        uc.mem_write(address, struct.pack("<I", command & ~(1 << 31)))


wrapper = r'''
#include "rtl9617c-mib.h"
int ni(const struct rtl_dq_io *io,unsigned int port,unsigned long long *raw)
{return rtl_ni_mib_read(io,port,raw);}
int epon(const struct rtl_dq_io *io,unsigned int *raw)
{return rtl_epon_mib_read(io,raw);}
void ni_pm(unsigned long long *total,const unsigned long long *raw,unsigned long long *pm)
{rtl_ni_mib_pm(total,raw,pm);}
void epon_pm(unsigned long long *total,const unsigned int *raw,unsigned long long *pm)
{rtl_epon_mib_pm(total,raw,pm);}
'''
read_fn = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
write_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
indirect_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)


class IO(ctypes.Structure):
    _fields_ = [("context", ctypes.c_void_p), ("read", read_fn), ("write", write_fn), ("indirect", indirect_fn)]


@read_fn
def read(context, reg):
    return state["memory"].get(reg, 0)


@write_fn
def write(context, reg, value):
    state["trace"].append((reg, value))
    state["memory"][reg] = value


@indirect_fn
def indirect(context, reg, command):
    write(context, reg, command)
    if state.get("failure"):
        return -110
    value = value_for(reg, command)
    if reg in (0xa1a4, 0xa1b0):
        state["memory"][reg + 4] = value >> 32
        state["memory"][reg + 8] = value & 0xffffffff
    else:
        state["memory"][reg + 4] = value
    return 0


io = IO(None, read, write, indirect)
records = []
with tempfile.TemporaryDirectory(prefix="epon-mib-", dir=root / "artifacts") as directory:
    directory = Path(directory)
    (directory / "native.c").write_text(wrapper)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-O2", "-shared", "-fPIC", "-I" + str(drivers),
                    str(directory / "native.c"), "-o", str(directory / "native.so")], check=True)
    native = ctypes.CDLL(str(directory / "native.so"))
    for port in (0, 3):
        state.update(stock_trace=[], trace=[], memory={}, failure=False)
        name = "aal_ni_eth_port_mib_get"
        oracle = ModuleOracle(args.stock, [name], callback)
        oracle.uc.mem_map(base, 0x10000)
        oracle.word(oracle.symbols["g_ne_reg_iobase"][0], base)
        oracle.uc.hook_add(UC_HOOK_MEM_READ, stock_read, begin=base, end=base + 0xffff)
        oracle.uc.hook_add(UC_HOOK_MEM_WRITE, stock_write, begin=base, end=base + 0xffff)
        oracle.call(name, 0, port, 0, oracle.scratch)
        original = struct.unpack("<48Q", oracle.uc.mem_read(oracle.scratch, 48 * 8))
        raw = (ctypes.c_ulonglong * 52)()
        assert native.ni(ctypes.byref(io), port, raw) == 0
        compact = list(raw[:24]) + [raw[25]] + list(raw[26:38]) + list(raw[39:49]) + [raw[51]]
        assert tuple(compact) == original
        assert state["trace"] == state["stock_trace"]
        totals = (ctypes.c_ulonglong * 52)()
        pm = (ctypes.c_ulonglong * 37)()
        native.ni_pm(totals, raw, pm)
        assert pm[2] == raw[51] and pm[3] == raw[25] and pm[5] == raw[0] + raw[1] + raw[2]
        assert pm[11] == raw[8] and pm[17] == raw[10] and pm[19] == raw[12]
        assert list(pm[20:26]) == list(raw[39:45]) and list(pm[26:32]) == list(raw[15:21])
        totals[8] = 0xfffffff0
        raw[8] = 0x10
        native.ni_pm(totals, raw, pm)
        assert pm[11] == 0x100000010
        state["failure"] = True
        assert native.ni(ctypes.byref(io), port, raw) == -110
        records.append({"NI_port": port, "accesses_and_values_match_stock": True,
                        "32bit_counter_wrap_extended": True, "timeout_propagates": True})
    state.update(stock_trace=[], trace=[], memory={0x604: 0}, failure=False)
    name = "aal_epon_mac_stats_port_eth_get"
    oracle = ModuleOracle(args.stock, [name, "aal_epon_mac_mib_rc_set"], callback)
    oracle.uc.mem_map(base, 0xc000)
    oracle.word(oracle.symbols["ca_venus_pon_reg_iobase"][0], base)
    oracle.uc.hook_add(UC_HOOK_MEM_READ, stock_read, begin=base, end=base + 0xbfff)
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, stock_write, begin=base, end=base + 0xbfff)
    oracle.call("aal_epon_mac_mib_rc_set", 0, 0, 0)
    oracle.call(name, 0, 0, oracle.scratch)
    original = struct.unpack("<42I", oracle.uc.mem_read(oracle.scratch, 42 * 4))
    raw = (ctypes.c_uint * 42)()
    assert native.epon(ctypes.byref(io), raw) == 0
    assert tuple(raw) == original and state["trace"] == state["stock_trace"]
    total = (ctypes.c_ulonglong * 42)()
    pm = (ctypes.c_ulonglong * 37)()
    native.epon_pm(total, raw, pm)
    assert pm[2] == raw[0] | raw[1] << 32 and pm[3] == raw[21] | raw[22] << 32
    assert pm[4] == raw[3] + raw[4] + raw[5] and pm[5] == raw[24] + raw[25] + raw[26]
    assert pm[10] == raw[2] and pm[11] == raw[23]
    assert list(pm[20:26]) == list(raw[11:17]) and list(pm[26:32]) == list(raw[32:38])
    state["failure"] = True
    assert native.epon(ctypes.byref(io), raw) == -110
    records.append({"EPON": "shared MAC counter format across all3 modes", "accesses_and_values_match_stock": True,
                    "actual_RX_TX_fields_retained": True, "timeout_propagates": True})
args.output.write_text(json.dumps({"stock_sha256": hashlib.sha256(args.stock.read_bytes()).hexdigest(),
    "scope": "Actual original AAL MMIO instructions and native production helpers; MIB RAM data/completion modeled; no physical traffic",
    "records": records}, indent=2) + "\n")
print("Physical MIB: NI/EPON accesses match original; actual RX/TX/64bit bytes, wrap extension and timeout propagation pass")
