#!/usr/bin/env python3
"""Trace the original ARM64 BOSA initializer with modeled I2C and file reads.

This runs only the named function in Unicorn. It performs no host I2C or device
access; the trace is an independent oracle for the native calibration sequence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis/python-deps"))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2
from unicorn.arm64_const import UC_ARM64_REG_X3, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP

MODELS = {"gn25l95": 201, "gn25l98": 202, "gn28l95": 203, "gn28l96": 204, "gn28l97": 205}


def trace(binary, calibration, model):
    function = ("Semtech_GN28L96_GN28L97_GN28L98_Initialization_From_MCU" if model >= 204 else
                "Semtech_GN25L95_GN25L98_GN28L95_Initialization_From_MCU")
    emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    with binary.open("rb") as stream:
        elf = ELFFile(stream)
        segments = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        low = min(s["p_vaddr"] for s in segments) & ~0xfff
        high = (max(s["p_vaddr"] + s["p_memsz"] for s in segments) + 0xfff) & ~0xfff
        emulator.mem_map(low, high - low)
        for segment in segments:
            emulator.mem_write(segment["p_vaddr"], segment.data())
        symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".dynsym").iter_symbols()}
    disassembly = subprocess.check_output(["aarch64-linux-gnu-objdump", "-d", str(binary)], text=True)
    hooks = {int(address, 16): name for address, name in
             re.findall(r"^([0-9a-f]+) <([^<>]+)@plt>:", disassembly, re.M)}
    hooks.update({symbols[name]: name for name in ("rtkbosa_byte_read", "rtkbosa_byte_write")})
    scratch = 0x20000000
    emulator.mem_map(scratch, 0x200000)
    emulator.mem_write(scratch, b"calibration.bin\0")
    emulator.reg_write(UC_ARM64_REG_SP, scratch + 0x1ff000)
    emulator.reg_write(UC_ARM64_REG_LR, scratch + 0x1000)
    emulator.reg_write(UC_ARM64_REG_X0, scratch)
    emulator.reg_write(UC_ARM64_REG_X1, model)
    page = 0
    registers = {}
    operations = []
    allocations = scratch + 0x10000
    read_values = {(0x81, 0x89): 2}

    def hook(uc, address, size, userdata):
        nonlocal page, allocations
        name = hooks.get(address)
        if name is None:
            return
        args = [uc.reg_read(reg) for reg in (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                            UC_ARM64_REG_X2, UC_ARM64_REG_X3)]
        result = 0
        if name == "rtkbosa_byte_write":
            slave, reg, value = args[0] & 0xff, args[1] & 0xff, args[2] & 0xff
            operations.append({"op": "write", "slave": slave, "page": page, "reg": reg, "value": value})
            if reg == 0x7f:
                page = value
            else:
                registers[(page if reg >= 0x80 else -1, reg)] = value
        elif name == "rtkbosa_byte_read":
            slave, reg = args[0] & 0xff, args[1] & 0xff
            value = read_values.get((page, reg), registers.get((page if reg >= 0x80 else -1, reg), 0))
            operations.append({"op": "read", "slave": slave, "page": page, "reg": reg, "value": value})
            uc.mem_write(args[2], bytes([value]))
        elif name in ("fopen", "fopen64"):
            result = scratch + 0x2000
        elif name == "malloc":
            result = allocations
            allocations += (args[0] + 15) & ~15
        elif name == "fread":
            data = calibration[:args[1] * args[2]]
            uc.mem_write(args[0], data)
            result = len(data) // args[1]
        elif name == "memcpy":
            uc.mem_write(args[0], bytes(uc.mem_read(args[1], args[2])))
            result = args[0]
        elif name not in ("puts", "printf", "fseek", "fclose", "free", "usleep", "sleep"):
            raise RuntimeError(f"Unmodeled original call: {name}")
        uc.reg_write(UC_ARM64_REG_X0, result)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    emulator.hook_add(UC_HOOK_CODE, hook)
    emulator.emu_start(symbols[function], scratch + 0x1000, count=2000000)
    if emulator.reg_read(UC_ARM64_REG_PC) != scratch + 0x1000:
        raise RuntimeError("Original initializer did not return within the instruction budget")
    return {"function": function, "function_address": hex(symbols[function]), "model": model,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "calibration_sha256": hashlib.sha256(calibration).hexdigest(),
            "scope": "original initializer emulated with I2C model; no hardware verification",
            "operations": operations}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("calibration", type=Path)
    parser.add_argument("model", choices=MODELS)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = trace(args.binary, args.calibration.read_bytes(), MODELS[args.model])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"model": args.model, "operations": len(result["operations"]),
                      "binary_sha256": result["binary_sha256"]}))
