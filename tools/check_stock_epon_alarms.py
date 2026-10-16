#!/usr/bin/env python3
"""Compare production CTC alarm hysteresis and wire encoding with stock ARM64 code."""
import argparse
import ctypes
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
    UC_ARM64_REG_X4, UC_ARM64_REG_D0, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP,
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
registers = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4)


def hook(uc, address, size, context):
    a, b, c, d = (uc.reg_read(r) for r in registers[:4])
    if address == 0x403400:  # memcpy
        uc.mem_write(a, bytes(uc.mem_read(b, c)))
        result = a
    elif address == 0x4038e0:  # htons
        result = (a & 255) << 8 | (a >> 8 & 255)
    elif address in calls:
        name = calls[address]
        if name in ("htons", "ntohs"):
            result = (a & 255) << 8 | (a >> 8 & 255)
        elif name in ("htonl", "ntohl"):
            result = int.from_bytes((a & 0xffffffff).to_bytes(4, "little"), "big")
        elif name == "malloc":
            result = 0x5006000
            uc.mem_write(result, bytes(a))
        elif name == "free":
            result = 0
        elif name == "__log10":
            value = struct.unpack("<d", struct.pack("<Q", uc.reg_read(UC_ARM64_REG_D0)))[0]
            value = math.log10(value) if value > 0 else -math.inf
            uc.reg_write(UC_ARM64_REG_D0, struct.unpack("<Q", struct.pack("<d", value))[0])
            result = 0
        else:
            raise AssertionError(name)
    elif address == 0x40e57c:  # logging only
        result = 0
    elif address == 0x42c5a4:  # configured threshold lookup for the Get encoder
        uc.mem_write(c, struct.pack("<I", 100))
        uc.mem_write(d, struct.pack("<I", 90))
        result = 0
    else:
        return
    uc.reg_write(UC_ARM64_REG_X0, result)
    uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))


# Resolve imported call addresses from this exact ELF, rather than a libc version.
calls = {}
with args.stock.open("rb") as stream:
    elf = ELFFile(stream)
    relocations = elf.get_section_by_name(".rela.plt")
    symbols = elf.get_section(relocations["sh_link"])
    plt = elf.get_section_by_name(".plt")["sh_addr"]
    for index, relocation in enumerate(relocations.iter_relocations()):
        name = symbols.get_symbol(relocation["r_info_sym"]).name
        if name in ("htons", "ntohs", "htonl", "ntohl", "malloc", "free", "__log10"):
            calls[plt + 32 + index * 16] = name
uc.hook_add(UC_HOOK_CODE, hook)


def call(address, *values):
    for register, value in zip(registers, values):
        uc.reg_write(register, value)
    uc.reg_write(UC_ARM64_REG_SP, 0x500ff00)
    uc.reg_write(UC_ARM64_REG_LR, 0x5008000)
    uc.emu_start(address, 0x5008000, count=10000)
    assert uc.reg_read(UC_ARM64_REG_PC) == 0x5008000


wrapper = r'''
#include <string.h>
#include "pon-alarm.h"
static struct pon_oam_alarm event;
static int poll(void *context, unsigned int generation, struct pon_oam_alarm *events, size_t capacity)
{ (void)context; (void)generation; (void)capacity; events[0]=event; return 1; }
int sample(unsigned int id, unsigned int threshold, unsigned int clear, unsigned int active, unsigned int raw)
{
    struct pon_alarm_entry entry={.value={.object_type=4,.id=id,.enabled=1,.threshold=threshold,
        .clear_threshold=clear,.active=active,.info_length=4}};
    pon_alarm_sample(&entry,raw);
    return entry.value.active;
}
int wire(unsigned int object, unsigned int instance, unsigned int id, unsigned int active,
         const unsigned char *info, unsigned int length, unsigned char *output)
{
    struct pon_oam_session s={.local_echoed=true,.extension_selected=true,.extension_version=0x30,
        .extension_oui={0x11,0x11,0x11}};
    struct pon_oam_identity identity={.alarms_poll=poll};
    struct pon_oam_alarm events[PON_OAM_ALARM_BATCH];
    size_t count;
    event=(struct pon_oam_alarm){.object_type=object,.instance=instance,.id=id,.active=active,.info_length=length};
    memcpy(event.info,info,length);
    return pon_oam_events(&s,&identity,events,&count,output);
}
int threshold(unsigned char *output)
{
    struct pon_alarm_table table;
    unsigned char id[]={1,1};
    struct pon_oam_request request={.branch=0xc7,.leaf=0x82,.parameters=id,.parameter_size=2};
    struct pon_oam_alarm alarm={.object_type=4,.id=0x101,.threshold=100,.clear_threshold=90};
    pon_alarm_init(&table);
    pon_alarm_access(&table,&alarm,PON_ALARM_THRESHOLD_SET);
    return pon_alarm_variable(&table,&request,0,0,output);
}
int delivery(void)
{
    struct pon_alarm_table table;
    struct pon_oam_alarm alarm={.object_type=4,.id=0x101,.enabled=1,.threshold=100,.clear_threshold=90};
    struct pon_oam_alarm events[2];
    pon_alarm_init(&table);
    pon_alarm_access(&table,&alarm,PON_ALARM_STATUS_SET);
    alarm.threshold=100; alarm.clear_threshold=90;
    pon_alarm_access(&table,&alarm,PON_ALARM_THRESHOLD_SET);
    pon_alarm_sample(&table.entries[0],101);
    if (pon_alarm_pending(&table,1,events,2)!=1) return 1;
    if (pon_alarm_pending(&table,1,events,2)!=1) return 2; /* failed send remains pending */
    pon_alarm_reported(&table,events,1);
    if (pon_alarm_pending(&table,1,events,2)) return 3;
    pon_alarm_sample(&table.entries[0],89);
    if (pon_alarm_pending(&table,1,events,2)!=1 || events[0].active) return 4;
    table.entries[0].sampled=false;
    if (pon_alarm_pending(&table,1,events,2)) return 5; /* failed sample cannot report stale data */
    return 0;
}
'''
records = []
with tempfile.TemporaryDirectory(prefix="epon-alarms-", dir=root / "artifacts") as directory:
    directory = Path(directory)
    (directory / "native.c").write_text(wrapper)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-O2", "-shared", "-fPIC", "-I" + str(source),
                    str(directory / "native.c"), str(source / "pon-alarm.c"), str(source / "pon-oam.c"),
                    "-o", str(directory / "native.so")], check=True)
    native = ctypes.CDLL(str(directory / "native.so"))
    assert native.delivery() == 0
    native.wire.argtypes = [ctypes.c_uint] * 4 + [ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p]
    for id in range(0x101, 0x115):
        field = (id - 0x101) % 10 // 2
        address = (0x42a678, 0x42a678, 0x42a8c4, 0x42ab0c, 0x42ad90)[field]
        high = id & 1
        threshold, clear = (100, 90) if high else (100, 110)
        samples = (90, 100, 101, 95, 90, 89) if high else (120, 100, 99, 105, 110, 111)
        if field == 4 and not high:
            threshold, clear, samples = 0xff00, 0, (0, 0xff00, 0xfeff, 0xff80, 0, 1)
        active = 0
        states = []
        for raw in samples:
            alarm = bytearray(36)
            struct.pack_into("<HH", alarm, 0, 4, id)
            alarm[16] = active
            struct.pack_into("<II", alarm, 20, threshold, clear)
            uc.mem_write(0x5000000, bytes(alarm))
            uc.mem_write(0x5001000, bytes(4))
            uc.mem_write(0x5002000, struct.pack("!H", raw))
            call(address, 0x5000000, 0x5001000, 0x5002000)
            original = struct.unpack("<I", uc.mem_read(0x5001000, 4))[0]
            assert native.sample(id, threshold, clear, active, raw) == original, (hex(id), raw, active)
            active = original
            states.append(active)
        records.append({"id": hex(id), "matches_actual_hysteresis": True, "states": states})
    for object, instance, id, info in ((4, 0, 0x101, b"\0\0\0\x65"), (1, 0x01000004, 0x302, b"")):
        event = bytearray(40)
        struct.pack_into("<H", event, 0, object)
        struct.pack_into("<I", event, 4, instance)
        struct.pack_into("<H", event, 8, id)
        event[16] = 0  # stock pre-C5 report state is inverted
        event[17:17 + len(info)] = info
        event[25] = len(info)
        uc.mem_write(0x5000000, bytes(event))
        uc.mem_write(0x47b0e8, struct.pack("<3I", 0x11, 0x11, 0x11))
        call(0x42bcec, 0x5000000, 0x5003000, 0x5004000)
        size = struct.unpack("<H", uc.mem_read(0x5004000, 2))[0]
        output = ctypes.create_string_buffer(1514)
        assert native.wire(object, instance, id, 1, info, len(info), output) == 60
        assert output.raw[20:20 + size] == bytes(uc.mem_read(0x5003000, size))
    container = struct.pack("<IH2xQ", 0x008200c7, 2, 0x5002000)
    uc.mem_write(0x5000000, container)
    uc.mem_write(0x5002000, b"\x01\x01")
    call(0x424ef8, 0, 0, 0, 0x008200c7, 0x5000000)
    original = bytes(uc.mem_read(0x5006000, 10))
    output = ctypes.create_string_buffer(10)
    assert native.threshold(output) == 10
    assert original == b"\x01\x01" + struct.pack("!II", 100, 100)
    assert output.raw == b"\x01\x01" + struct.pack("!II", 100, 90)
    records.append({"event_tlv_matches_stock": True, "stock_repeated_threshold_bug_reproduced": True,
                    "native_returns_configured_clear_threshold": True, "failed_send_stays_pending": True,
                    "failed_sample_does_not_report_stale_data": True})
args.output.write_text(json.dumps({"stock_sha256": hashlib.sha256(args.stock.read_bytes()).hexdigest(),
    "scope": "Actual optical alarm predicates and event/Get encoders; libc/logging and threshold lookup modeled; no physical sensor or OLT test",
    "records": records}, indent=2) + "\n")
print("CTC alarms:20 stock hysteresis predicates/event TLVs match; real clear-threshold value replaces the reproduced stock bug")
