#!/usr/bin/env python3
"""Compare LOID response bytes with the board original eponoamd ARM64 encoder."""
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "analysis/python-deps"))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP

original = root / "evidence/rtl-workspace/private/tyfza1/selected/ubi_r0/bin/eponoamd"
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
uc.mem_map(0x400000, 0x100000)
uc.mem_map(0x5000000, 0x10000)
with original.open("rb") as stream:
    for segment in ELFFile(stream).iter_segments():
        if segment["p_type"] == "PT_LOAD":
            uc.mem_write(segment["p_vaddr"], segment.data())


def libc(uc, address, size, context):
    if address not in (0x403480, 0x4038e0, 0x403800, 0x403400):
        return
    a, b, c = (uc.reg_read(register) for register in
               (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2))
    if address == 0x403480:
        result = bytes(uc.mem_read(a, 64)).index(0)
    elif address == 0x4038e0:
        result = (a & 255) << 8 | (a >> 8 & 255)
    elif address == 0x403800:
        uc.mem_write(a, bytes([b & 255]) * c)
        result = a
    else:
        uc.mem_write(a, bytes(uc.mem_read(b, c)))
        result = a
    uc.reg_write(UC_ARM64_REG_X0, result)
    uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))


uc.hook_add(UC_HOOK_CODE, libc)
source = root / "port/packages/tyfza1-pon-control/src"
wrapper = r"""
#include <string.h>
#include "pon-oam.h"
int native(const unsigned char *loid, const unsigned char *password, unsigned int auth_type, unsigned char *output)
{
    struct pon_oam_identity identity = {.loid_configured = true};
    struct pon_oam_session session = {.local_echoed = true};
    unsigned char input[60] = {};
    memcpy(identity.loid, loid, 24);
    memcpy(identity.password, password, 12);
    input[12] = 0x88; input[13] = 9; input[14] = 3; input[17] = 0xfe;
    memcpy(input + 18, "\x11\x11\x11\x05\x01\x00\x01\x01", 8);
    input[25] = auth_type;
    return pon_oam_receive(&session, &identity, input, sizeof(input), output);
}
"""
records = []
with tempfile.TemporaryDirectory(prefix="stock-oam-auth-", dir=root / "artifacts") as work:
    work = Path(work)
    (work / "native.c").write_text(wrapper)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-I" + str(source),
                    str(work / "native.c"), str(source / "pon-oam.c"), "-o", str(work / "native.so")], check=True)
    native = ctypes.CDLL(str(work / "native.so")).native
    native.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p]
    for loid, password in ((b"0012345678", b""), (b"TEST-LOID", b"secret"),
                           (b"123456789012345678901234", b"123456789012")):
        uc.mem_write(0x5000000, loid.ljust(30, b"\0") + password.ljust(30, b"\0"))
        uc.mem_write(0x5000100, bytes(64))
        for register, value in ((UC_ARM64_REG_X0, 0x5000000), (UC_ARM64_REG_X1, 0x5000100),
                                (UC_ARM64_REG_X2, 0x5000200), (UC_ARM64_REG_SP, 0x500ff00),
                                (UC_ARM64_REG_LR, 0x5008000)):
            uc.reg_write(register, value)
        uc.emu_start(0x4164fc, 0x5008000, count=1000)
        assert uc.reg_read(UC_ARM64_REG_PC) == 0x5008000
        length = struct.unpack("<H", uc.mem_read(0x5000200, 2))[0]
        expected = bytes(uc.mem_read(0x5000100, length))
        output = ctypes.create_string_buffer(1514)
        assert native(loid.ljust(24, b"\0"), password.ljust(12, b"\0"), 1, output) == 62
        assert length == 40 and output.raw[22:62] == expected, (len(loid), len(password))
        records.append({"loid_bytes": len(loid), "password_bytes": len(password),
                        "auth_response_bytes": length, "matches_board_original": True})
    for auth_type in (2, 255):
        uc.reg_write(UC_ARM64_REG_X0, 1)
        uc.reg_write(UC_ARM64_REG_X1, 0x5000100)
        uc.reg_write(UC_ARM64_REG_X2, 0x5000200)
        uc.reg_write(UC_ARM64_REG_SP, 0x500ff00)
        uc.reg_write(UC_ARM64_REG_LR, 0x5008000)
        uc.emu_start(0x416670, 0x5008000, count=1000)
        assert uc.reg_read(UC_ARM64_REG_PC) == 0x5008000
        length = struct.unpack("<H", uc.mem_read(0x5000200, 2))[0]
        output = ctypes.create_string_buffer(1514)
        assert native(bytes(24), bytes(12), auth_type, output) == 60
        assert length == 5 and output.raw[22:27] == bytes(uc.mem_read(0x5000100, length))
        records.append({"unsupported_auth_type": auth_type, "requested_auth_type": 1,
                        "matches_board_original": True})
report = {"original_sha256": hashlib.sha256(original.read_bytes()).hexdigest(), "original": "eponoamd:0x4164fc", "libc_only_modelled": True,
          "hardware_test": False, "records": records}
(root / "logs/stock-oam-auth-contract.json").write_text(json.dumps(report, indent=2) + "\n")
print("Native LOID response matches original eponoamd: blank/short/full credentials and unsupported-type NAK")
