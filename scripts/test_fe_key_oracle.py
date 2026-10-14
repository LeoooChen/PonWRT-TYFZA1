#!/usr/bin/env python3
"""Check native IPv4 flow keys/CRC against the board's original ARM64 function."""
import ctypes
import hashlib
import json
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "analysis/python-deps"))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_SP_EL0

stock = root / "analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko"
reference = root / "evidence/rtl-workspace/downloads/reference/ca-network-drivers-20261005/ca-network-engine/1.0-r0/ca-network-engine-1.0/aal-77c/include/aal_common.h"
header = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-fe-format.h"
text = reference.read_text()


def layout(name):
    block = text.split(" * hash " + name + "\n", 1)[1].split("aal_hash_" + name + "_t;", 1)[0]
    fields, offset = {}, 0
    for field, width in re.findall(r"ca_uint64_t\s+(\w+)\s*:\s*(\d+)", block):
        fields[field] = offset, int(width)
        offset += int(width)
    return fields, (offset + 7) // 8


def set_value(data, offset, width, value):
    for i in range(width):
        mask = 1 << ((offset + i) % 8)
        index = (offset + i) // 8
        data[index] = (data[index] & ~mask) | (mask if value & (1 << i) else 0)


key_layout, key_size = layout("key")
mask_layout, mask_size = layout("mask")
emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
sections, imported = {}, {}
next_section = 0x1000000
with stock.open("rb") as stream:
    elf = ELFFile(stream)
    for number, section in enumerate(elf.iter_sections()):
        if not section["sh_flags"] & 2 or not section["sh_size"]:
            continue
        size = (section["sh_size"] + 4095) & ~4095
        sections[number] = next_section
        emulator.mem_map(next_section, size)
        if section["sh_type"] != "SHT_NOBITS":
            emulator.mem_write(next_section, section.data())
        next_section += size
    symbols = elf.get_section_by_name(".symtab")
    function = symbols.get_symbol_by_name("hash_value_calculate")[0]
    target_section = function["st_shndx"]
    function_start, function_end = function["st_value"], function["st_value"] + function["st_size"]
    start = sections[target_section] + function_start
    emulator.mem_map(0x4000000, 0x10000)
    for section in elf.iter_sections():
        if section["sh_type"] != "SHT_RELA" or section["sh_info"] != target_section:
            continue
        for relocation in section.iter_relocations():
            offset = relocation["r_offset"]
            if not function_start <= offset < function_end:
                continue
            symbol = symbols.get_symbol(relocation["r_info_sym"])
            number = symbol["st_shndx"]
            if number == "SHN_UNDEF":
                if symbol.name not in imported:
                    imported[symbol.name] = 0x4000000 + len(imported) * 256
                value = imported[symbol.name]
            else:
                value = sections[number] + symbol["st_value"]
            value += relocation["r_addend"]
            place = sections[target_section] + offset
            instruction = struct.unpack("<I", emulator.mem_read(place, 4))[0]
            kind = relocation["r_info_type"]
            if kind == 275:  # ADRP page-relative address.
                immediate = ((value >> 12) - (place >> 12)) & 0x1fffff
                instruction = (instruction & ~0x60ffffe0) | ((immediate & 3) << 29) | ((immediate >> 2) << 5)
            elif kind in (277, 285, 286, 284):
                shift = {277: 0, 285: 2, 286: 3, 284: 1}[kind]
                instruction = (instruction & ~(0xfff << 10)) | (((value & 0xfff) >> shift) << 10)
            elif kind in (282, 283):  # B/BL.
                instruction = (instruction & ~0x3ffffff) | (((value - place) >> 2) & 0x3ffffff)
            else:
                raise AssertionError(("unhandled relocation", kind, symbol.name))
            emulator.mem_write(place, struct.pack("<I", instruction))

scratch, end = 0x5000000, 0x5008000
emulator.mem_map(scratch, 0x10000)
emulator.reg_write(UC_ARM64_REG_SP_EL0, scratch + 0x9000)
import_addresses = {address: name for name, address in imported.items() if name != "ca_aal_debug"}


def unexpected_call(uc, address, size, unused):
    if address in import_addresses:
        raise AssertionError(("unexpected original call", import_addresses[address]))


emulator.hook_add(UC_HOOK_CODE, unexpected_call)
mask = bytearray(b"\xff" * mask_size)
for name, value in {"l4_dp_exact_range": 0, "l4_sp_exact_range": 0, "ip_sa": 32, "ip_da": 32,
                    "ip_protocol": 0, "ip_vld": 0, "ip_ver": 0, "orig_lspid": 0, "lspid": 0,
                    "pol_id": 0, "ctrl_set_id": 0, "ip_ttl": 0}.items():
    set_value(mask, *mask_layout[name], value)
fields = re.findall(r"RTL_FE_KEY_(\w+),", header.read_text().split("enum rtl_fe_key_field {", 1)[1].split("};", 1)[0])
fields = [name.lower() for name in fields if name != "FIELD_COUNT"]
rng = random.Random(0x9617c)
cases = []
for i in range(160):
    cases.append({"l4_dp_exact_range": rng.getrandbits(16), "l4_sp_exact_range": rng.getrandbits(16),
                  "ip_da_0": rng.getrandbits(32), "ip_sa_0": rng.getrandbits(32), "ip_protocol": 6 if i % 2 else 17,
                  "ip_vld": 1, "pol_id": i % 32, "orig_lspid": 7 if i % 2 else 0,
                  "lspid": 7 if i % 2 else 0, "ctrl_set_id": i % 4, "table_id": 1, "ip_ttl": (i * 13) & 255})
for i in range(160):
    tagged = dict(cases[i])
    count = i % 3
    tagged.update(vlan_cnt=count,top_vid=(i * 4095 // 159) if count else 0,
                  top_tpid_enc=1+(i%2) if count else 0,
                  inner_vid=(4095-i*4095//159) if count==2 else 0,
                  inner_tpid_enc=1+(i%2) if count==2 else 0)
    cases.append(tagged)
for id in range(1, 61):
    wireless = dict(cases[0], lspid=18, orig_lspid=18,
                    mdata=0x123456789abc0000 | id)
    cases.append(wireless)
with tempfile.TemporaryDirectory(prefix="fe-key-", dir=root / "artifacts") as temporary:
    temporary = Path(temporary)
    source = temporary / "key.c"
    source.write_text('#include "rtl9617c-fe-format.h"\n'
        'void set(unsigned int *out,unsigned int field,unsigned int value) {rtl_fe_key_set(out,field,value);}\n'
        'void crc(unsigned int *key,unsigned int *c32,unsigned short *c16) {rtl_fe_key_crc(key,c32,c16);}\n')
    library = temporary / "key.so"
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-O2", "-I",
                    str(header.parent), str(source), "-o", str(library)], check=True)
    native = ctypes.CDLL(str(library))
    native.set.argtypes = [ctypes.POINTER(ctypes.c_uint), ctypes.c_uint, ctypes.c_uint]
    native.crc.argtypes = [ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_ushort)]
    for case in cases:
        original_key = bytearray(key_size)
        for name, value in case.items():
            set_value(original_key, *key_layout[name], value)
        native_key = (ctypes.c_uint * 23)()
        for index, name in enumerate(fields):
            value = case.get('mdata', 0) & 255 if name == 'mdata_byte' else case.get(name, 0)
            native.set(native_key, index, 0 if name == "table_id" else value)
        emulator.mem_write(scratch, bytes(original_key))
        case_mask = bytearray(mask)
        if 'vlan_cnt' in case:
            for name in ('vlan_cnt','top_vid','top_tpid_enc','inner_vid','inner_tpid_enc'):
                set_value(case_mask,*mask_layout[name],0)
        if 'mdata' in case:
            set_value(case_mask, *mask_layout['mdata'], 0xffffffffffffff00)
        emulator.mem_write(scratch + 0x1000, bytes(case_mask))
        emulator.mem_write(scratch + 0x2000, bytes(32))
        for register, value in ((UC_ARM64_REG_X0, scratch), (UC_ARM64_REG_X1, scratch + 0x1000),
                                (UC_ARM64_REG_X2, scratch + 0x2000), (UC_ARM64_REG_SP, scratch + 0xf000),
                                (UC_ARM64_REG_LR, end)):
            emulator.reg_write(register, value)
        emulator.emu_start(start, end, count=100000)
        assert emulator.reg_read(UC_ARM64_REG_PC) == end and emulator.reg_read(UC_ARM64_REG_X0) == 0
        masked_key = bytes(emulator.mem_read(scratch, key_size))
        assert masked_key == bytes(native_key), (case, masked_key.hex(), bytes(native_key).hex())
        actual = bytes(emulator.mem_read(scratch + 0x2000, 8))
        original_crc32, original_crc16 = struct.unpack_from("<IH", actual)
        c32, c16 = ctypes.c_uint(), ctypes.c_ushort()
        native.crc(native_key, ctypes.byref(c32), ctypes.byref(c16))
        assert (c32.value, c16.value) == (original_crc32, original_crc16), (case, (c32.value, c16.value), actual.hex())

record = {"stock_module_sha256": hashlib.sha256(stock.read_bytes()).hexdigest(),
          "native_header_sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
          "vectors": len(cases), "VLAN_QinQ_vectors":160,"wifi_metadata_vectors":60,
          "key_bytes": key_size, "mask_bytes": mask_size,
          "actual_stock_masked_key_and_CRC16_CRC32_match": True,
          "protocols": ["IPv4 TCP", "IPv4 UDP"], "LLIDs": 32, "profiles": 4,
          "tuple_rotation_mode": "neutral", "hardware_access": False}
(root / "logs/fe-key-oracle.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
