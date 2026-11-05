#!/usr/bin/env python3
"""Compare native CTC containers with the board's actual ARM64 encoder."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
    UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--stock", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source = root / "port/packages/tyfza1-pon-control/src"
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
uc.mem_map(0x400000, 0x100000)
uc.mem_map(0x5000000, 0x10000)
with args.stock.open("rb") as stream:
    for segment in ELFFile(stream).iter_segments():
        if segment["p_type"] == "PT_LOAD":
            uc.mem_write(segment["p_vaddr"], segment.data())


def libc(uc, address, size, context):
    a, b, c = (uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2))
    if address == 0x403400:  # memcpy
        uc.mem_write(a, bytes(uc.mem_read(b, c)))
        result = a
    elif address == 0x4038e0:  # htons
        result = (a & 255) << 8 | (a >> 8 & 255)
    else:
        return
    uc.reg_write(UC_ARM64_REG_X0, result)
    uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))


uc.hook_add(UC_HOOK_CODE, libc)
wrapper = r'''
#include <assert.h>
#include <string.h>
#include "pon-oam.h"
static unsigned int amount, calls;
static int get(void *context, const struct pon_oam_request *r, unsigned char *v, size_t capacity)
{
    (void)context;
    assert(r->version == 0x30 && capacity >= amount);
    if (r->leaf == 0x82) {
        assert(r->parameter_size == 2 && r->parameters[0] == 1 && r->parameters[1] == 2);
    } else assert(!r->parameter_size);
    for (unsigned int i = 0; i < amount; i++) v[i] = i;
    calls++;
    return amount;
}
static unsigned char set(void *context, const struct pon_oam_request *r,
                         const unsigned char *v, size_t size)
{
    (void)context;
    if (r->branch == 9 || r->branch == 0xc9) assert(!size);
    else {
        assert(size == amount);
        for (unsigned int i = 0; i < amount; i++) assert(v[i] == (unsigned char)i);
    }
    calls++;
    return 0x80;
}
int native(const unsigned char *payload, unsigned int length, unsigned int width, unsigned char *output)
{
    struct pon_oam_session s = {.local_echoed=true, .extension_version=0x30};
    struct pon_oam_identity id = {.get_parameter=get, .set_parameter=set};
    unsigned char frame[PON_OAM_FRAME_MAX] = {};
    frame[12]=0x88; frame[13]=9; frame[14]=3; frame[17]=0xfe;
    frame[18]=frame[19]=frame[20]=0x11;
    memcpy(frame+21, payload, length);
    amount=width; calls=0;
    return pon_oam_receive(&s, &id, frame, length+21, output);
}
unsigned int count(void) { return calls; }
'''
records = []
with tempfile.TemporaryDirectory(prefix="epon-containers-", dir=root / "artifacts") as work:
    work = Path(work)
    (work / "native.c").write_text(wrapper)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-I" + str(source),
                    str(work / "native.c"), str(source / "pon-oam.c"), "-o", str(work / "native.so")], check=True)
    library = ctypes.CDLL(str(work / "native.so"))
    native = library.native
    native.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    for width in (127, 128, 256, 296):
        data = bytes(i & 255 for i in range(width))
        uc.mem_write(0x5000000, struct.pack("<BxHH2xQ", 0xc7, 0xb2, width, 0x5001000))
        uc.mem_write(0x5001000, data)
        for register, value in ((UC_ARM64_REG_X0, 0x5000000), (UC_ARM64_REG_X1, 0x5002000),
                                (UC_ARM64_REG_X2, 0x5003000), (UC_ARM64_REG_SP, 0x500ff00),
                                (UC_ARM64_REG_LR, 0x5008000)):
            uc.reg_write(register, value)
        uc.emu_start(0x4162ec, 0x5008000, count=10000)
        assert uc.reg_read(UC_ARM64_REG_PC) == 0x5008000
        size = struct.unpack("<H", uc.mem_read(0x5003000, 2))[0]
        expected = bytes(uc.mem_read(0x5002000, size))
        output = ctypes.create_string_buffer(1514)
        assert native(b"\x01\xc7\x00\xb2\0", 5, width, output) == 22 + size
        assert output.raw[22:22 + size] == expected
        assert library.count() == 1
        if width >= 128:
            request = b"\x03" + expected + b"\0"
            assert native(request, len(request), width, output) == 60
            assert output.raw[22:26] == b"\xc7\x00\xb2\x80" and library.count() == 1
            assert native(request, len(request) - 2, width, output) < 0
        records.append({"value_bytes": width, "wire_bytes": size, "matches_stock_encoder": True})
    output = ctypes.create_string_buffer(1514)
    # The alarm ID must be consumed before the next variable is parsed.
    request = b"\x01\xc7\x00\x82\x02\x01\x02\x07\x01\x39\0"
    assert native(request, len(request), 10, output) == 60 and library.count() == 2
    assert output.raw[22:25] == b"\xc7\x00\x82" and output.raw[36:39] == b"\x07\x01\x39"
    for request in (b"\x03\x09\x00\x0b\0", b"\x03\xc9\x00\x01\0"):
        assert native(request, len(request), 0, output) == 60 and library.count() == 1
    records.append({"parameterized_get": True, "descriptor_only_actions": True})
args.output.write_text(json.dumps({"stock_sha256": hashlib.sha256(args.stock.read_bytes()).hexdigest(),
    "encoder": "eponoamd:0x4162ec", "modeled": "libc memcpy/htons only; native backend callbacks use synthetic data",
    "hardware_test": False, "records": records}, indent=2) + "\n")
print("CTC container boundaries match stock; parameterized Get and descriptor-only actions pass")
