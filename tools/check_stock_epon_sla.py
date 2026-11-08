#!/usr/bin/env python3
"""Compare SLA buckets with the actual DAL/AAL; check one native policy update."""
import ctypes
from pathlib import Path
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ

root = Path(__file__).resolve().parents[1]
drivers = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
stock = Path("/home/leo/rea-analysis-20261016-work/rootfs/lib/modules/5.10.70/extra")
base = 0x6000000
buckets = {}


def dal_callback(name, args):
    if name == "aal_puc_voq_map_cfg_get":
        dal.uc.mem_write(args[1], bytes(4))
    elif name == "dal_rt_rtl8277c_ponmisc_tcont_llid_logic2phy_get":
        dal.uc.mem_write(args[1], struct.pack("<H", args[0]))
    elif name == "aal_puc_voq_sche_cfg_get":
        dal.uc.mem_write(args[1], bytes(16))
    elif name in ("aal_puc_voq_tbc_mem_set", "aal_puc_pir_voq_tbc_mem_set"):
        assert args[2] == 0x77
        buckets[name] = bytes(dal.uc.mem_read(args[3], 28))
    elif name not in ("aal_puc_voq_drop_set", "aal_puc_port_voq_map_set",
                      "aal_puc_port_voq_sche_info_tbl_set", "aal_puc_voq_valid_set", "printk"):
        raise AssertionError(name)
    return 0


dal = ModuleOracle(stock / "ca-rtk.ko", ["dal_rtl8277c_ponmac_queue_add"], dal_callback,
                   external_symbols={"ca_rtk_debug": 0x5007000, "hal_ctrl": 0x5007100})
dal.uc.mem_write(dal.symbols["ponmac_init"][0], struct.pack("<I", 1))
dal.word(0x5007118, 0x5001000)
dal.word(0x5001020, 0x5001200)
dal.uc.mem_write(0x500133c, struct.pack("<I", 32))


def aal_callback(name, args):
    assert name in ("ca_spin_lock", "ca_spin_unlock", "printk")
    return 0


aal = ModuleOracle(stock / "ca-ne.ko",
                   ["aal_puc_voq_tbc_mem_set", "aal_puc_pir_voq_tbc_mem_set"], aal_callback)
aal.uc.mem_map(base, 0x10000)
aal.word(aal.symbols["ca_venus_pon_reg_iobase"][0], base)


def complete(uc, access, address, size, value, context):
    if address - base in (0x81c4, 0x81ec):
        word = struct.unpack("<I", uc.mem_read(address, 4))[0]
        uc.mem_write(address, struct.pack("<I", word & 0x7fffffff))


aal.uc.hook_add(UC_HOOK_MEM_READ, complete, begin=base, end=base + 65535)
read_fn = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
write_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
access_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)


class IO(ctypes.Structure):
    _fields_ = [("context", ctypes.c_void_p), ("read", read_fn), ("write", write_fn), ("indirect", access_fn)]


memory, tables, lengths = {}, {}, {}
failure = False
registers = {0x8000: list(range(0x8014, 0x8000, -4)),
             0x8060: list(range(0x8074, 0x8060, -4)),
             0x81c4: [0x81cc, 0x81c8], 0x81ec: [0x81f4, 0x81f0]}


@read_fn
def read(context, reg):
    return memory.get(reg, 0)


@write_fn
def write(context, reg, value):
    memory[reg] = value


@access_fn
def access(context, reg, command):
    if failure and reg == 0x81ec:
        return -110
    row = command & 255
    if reg == 0x802c:
        memory[0x8034] = lengths.get(row, 0)
        memory[0x8030] = 0
    elif reg == 0x80dc:
        lengths[row] = 0
    elif command & 0x40000000:
        tables[reg, row] = [memory.get(r, 0) for r in registers[reg]]
    else:
        for r, v in zip(registers[reg], tables.get((reg, row), [0] * len(registers[reg]))):
            memory[r] = v
    return 0


io = IO(None, read, write, access)
wrapper = '''
#include <errno.h>
#include "rtl9617c-epon-sla.h"
int bucket(const struct rtl_dq_io *io,unsigned int q,unsigned int rate,int pir)
{return rtl_epon_sla_bucket(io,q,rate,pir);}
int apply(const struct rtl_dq_io *io,unsigned int p,const unsigned char *data,unsigned int size)
{int ret=rtl_epon_sla_validate(data,size);return ret?ret:rtl_epon_sla_apply(io,p,data);}
'''
with tempfile.TemporaryDirectory(prefix="sla-", dir=root / "artifacts") as tmp:
    tmp = Path(tmp)
    (tmp / "sla.c").write_text(wrapper)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-I" + str(drivers),
                    str(tmp / "sla.c"), "-o", str(tmp / "sla.so")], check=True)
    native = ctypes.CDLL(str(tmp / "sla.so"))
    native.bucket.argtypes = [ctypes.POINTER(IO), ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
    native.apply.argtypes = [ctypes.POINTER(IO), ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint]
    for rate in (0, 24992, 25000, 49992, 50000, 499992, 500000, 524280, 0x1f3ffff):
        dal.uc.mem_write(dal.scratch, struct.pack("<2I", 1, 2))
        dal.uc.mem_write(dal.scratch + 32, struct.pack("<5I", rate, rate, 0, 0, 0))
        assert dal.call("dal_rtl8277c_ponmac_queue_add", dal.scratch, dal.scratch + 32) == 0
        for pir, name in enumerate(("aal_puc_voq_tbc_mem_set", "aal_puc_pir_voq_tbc_mem_set")):
            reg = 0x81ec if pir else 0x81c4
            tables[reg, 10] = [0x12345678, 0x98765480]
            for r, v in zip(registers[reg], tables[reg, 10]):
                aal.uc.mem_write(base + r, struct.pack("<I", v))
            aal.uc.mem_write(aal.scratch, buckets[name])
            assert aal.call(name, 0, 10, 0x77, aal.scratch) == 0
            assert native.bucket(ctypes.byref(io), 10, rate, pir) == 0
            expected = [struct.unpack("<I", aal.uc.mem_read(base + r, 4))[0] for r in registers[reg]]
            assert tables[reg, 10] == expected, (rate, pir, tables[reg, 10], expected)
    packet = bytes([1, 1, 0, 0, 0, 0, 0, 2])
    packet += struct.pack("!B4HB", 0, 0, 0, 100, 1000, 3)
    packet += struct.pack("!B4HB", 1, 0, 0, 200, 2000, 7)
    memory[0x81bc] = 0xffffffff
    memory[0x80e0] = 0x12345678
    memory[0x8090] = 0x55555555
    lengths[8] = (1 << 21) | 100
    assert native.apply(ctypes.byref(io), 1, packet, len(packet)) == 0
    assert not lengths[8] and memory[0x80e0] == 0x12345678 and memory[0x8090] == 0x55555555
    assert memory[0x81bc] == 0xffff03ff
    assert tables[0x8000, 1][2] >> 16 == 0x0703
    assert tables[0x81ec, 8][0] & 0x3ffffff == (8000 // 500) << 10
    assert tables[0x81c4, 8][0] & 0x3ffffff == 800 % 500 | (800 // 500) << 10
    assert native.apply(ctypes.byref(io), 1, b"\0", 1) == 0
    assert memory[0x81bc] == 0xffffffff and tables[0x81ec, 15][0] == 0xffffffff
    assert tables[0x8000, 1][2] >> 16 == 0
    unsupported = packet[:1] + b"\2" + packet[2:]
    assert native.apply(ctypes.byref(io), 1, unsupported, len(unsupported)) == -22
    assert native.apply(ctypes.byref(io), 1, packet[:-1], len(packet) - 1) == -22
    failure = True
    assert native.apply(ctypes.byref(io), 1, packet, len(packet)) == -110
    assert memory[0x81bc] & 0xff00 == 0
print("SLA: actual DAL/AAL rate/burst packing matches; native SP/WRR, queue purge, disable and failed-write gating passed")
