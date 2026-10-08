#!/usr/bin/env python3
"""Compare native L3FE action packing with this board's original ARM64 converter."""
import ctypes
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "analysis/python-deps"))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP

reference = root / "evidence/rtl-workspace/downloads/reference/ca-network-drivers-20261005/ca-network-engine/1.0-r0/ca-network-engine-1.0/aal-77c/include/aal_common.h"
binary = root / "analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko"
header = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-fe-format.h"
text = reference.read_text()


def group(number):
    start = text.index("#define HASH_ACTION_GROUP_%02d" % number)
    end = text.index("\n\n", start + 1)
    return [(name, int(width)) for name, width in
            re.findall(r"ca_uint64_t\s+(\w+)\s*:\s*(\d+)", text[start:end])]


def put_bits(data, bit, width, value):
    for i in range(width):
        if value & (1 << i):
            data[(bit + i) // 8] |= 1 << ((bit + i) % 8)


source_layout = {}
bit = 0
for number in list(range(23)) + [24]:
    for name, width in group(number):
        source_layout[name] = bit, width
        bit += width
fields = group(18) + group(20)
assert source_layout["mrr_vld"][0] == 770, source_layout["mrr_vld"]
cases = [{name: 0 for name, _ in fields}]
for name, width in fields:
    for value in (1, 1 << (width - 1), (1 << width) - 1):
        cases.append({name: value})
rng = random.Random(0x9617c)
cases += [{name: rng.getrandbits(width) for name, width in fields} for _ in range(128)]

with binary.open("rb") as stream:
    elf = ELFFile(stream)
    section = elf.get_section_by_name(".text")
    symbol = elf.get_section_by_name(".symtab").get_symbol_by_name("convert_act_flow_nomal_mode")[0]
    original = section.data()
    address = symbol["st_value"]

emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
code_base, scratch = 0x1000000, 0x2000000
emulator.mem_map(code_base, (len(original) + 4095) & ~4095)
emulator.mem_write(code_base, original)
emulator.mem_map(scratch, 0x10000)
end = scratch + 0x8000

with tempfile.TemporaryDirectory(prefix="fe-format-", dir=root / "artifacts") as directory:
    directory = Path(directory)
    source = directory / "format.c"
    source.write_text('#include "rtl9617c-fe-format.h"\n'
        'void pack(unsigned int *out, unsigned int field, unsigned int value) { rtl_fe_action_set(out, field, value); }\n')
    library = directory / "format.so"
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-O2",
                    "-I", str(header.parent), str(source), "-o", str(library)], check=True)
    native = ctypes.CDLL(str(library))
    native.pack.argtypes = [ctypes.POINTER(ctypes.c_uint), ctypes.c_uint, ctypes.c_uint]
    for case_number, case in enumerate(cases):
        vendor_input = bytearray((bit + 7) // 8)
        native_output = (ctypes.c_uint * 8)()
        for field_number, (name, width) in enumerate(fields):
            value = case.get(name, 0)
            offset, original_width = source_layout[name]
            assert width == original_width
            put_bits(vendor_input, offset, width, value)
            native.pack(native_output, field_number, value)
        emulator.mem_write(scratch, bytes(vendor_input))
        emulator.mem_write(scratch + 0x1000, bytes(32))
        emulator.reg_write(UC_ARM64_REG_X0, scratch)
        emulator.reg_write(UC_ARM64_REG_X1, scratch + 0x1000)
        emulator.reg_write(UC_ARM64_REG_SP, scratch + 0xf000)
        emulator.reg_write(UC_ARM64_REG_LR, end)
        emulator.emu_start(code_base + address, end, count=5000)
        assert emulator.reg_read(UC_ARM64_REG_PC) == end
        actual = bytes(emulator.mem_read(scratch + 0x1000, 32))
        expected = bytes(native_output)
        assert actual == expected, (case_number, case, actual.hex(), expected.hex())

record = {"scope": "actual stock ARM64 conversion emulated offline; no original module loaded",
          "native_header_sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
          "stock_module_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
          "GPL_layout_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
          "action_groups": [18, 20], "action_bits": 224, "slot_bytes": 32,
          "fields": len(fields), "vectors": len(cases), "all_match_stock_machine_code": True,
          "hardware_forwarding_verified": False}
(root / "logs/fe-action-oracle.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
