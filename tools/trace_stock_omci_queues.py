#!/usr/bin/env python3
"""Execute board-original queue comparator and OMCI-to-RTK queue-number handoff."""
from functools import cmp_to_key
import hashlib
import json
from pathlib import Path
import struct

from arm64_module_oracle import ModuleOracle
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_PC

root = Path(__file__).resolve().parents[1]
stock = root / "evidence/rtl-workspace/private/tyfza1/selected/ubi_r0"
library = stock / "lib/libomci_mib.so"
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
uc.mem_map(0, 0x60000)
uc.mem_map(0x5000000, 0x10000)
with library.open("rb") as stream:
    for segment in ELFFile(stream).iter_segments():
        if segment["p_type"] == "PT_LOAD":
            uc.mem_write(segment["p_vaddr"], segment.data())


def compare(left, right):
    # qdb row: T-CONT, ME index, WRR flag, SP priority, remaining fields.
    for address, row in ((0x5000000, left), (0x5000100, right)):
        uc.mem_write(address, struct.pack("<HHIH", 1, row[0], row[2], row[1]) + bytes(22))
    for register, value in ((UC_ARM64_REG_X0, 0x5000000), (UC_ARM64_REG_X1, 0x5000100),
                            (UC_ARM64_REG_SP, 0x500ff00), (UC_ARM64_REG_LR, 0x5008000)):
        uc.reg_write(register, value)
    uc.emu_start(0x19aa8, 0x5008000, count=100)
    assert uc.reg_read(UC_ARM64_REG_PC) == 0x5008000
    result = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
    return result if result < 0x80000000 else result - 0x100000000


records = []
for name, rows, expected in (
    ("SP priority descending", [(0, 7, 0), (1, 6, 0)], [0, 1]),
    ("OLT changes SP priority", [(0, 5, 0), (1, 6, 0)], [1, 0]),
    ("WRR before SP", [(0, 7, 0), (1, 0, 1)], [1, 0]),
    ("equal SP priority by ME index", [(1, 7, 0), (0, 7, 0)], [0, 1]),
):
    order = [row[0] for row in sorted(rows, key=cmp_to_key(compare))]
    assert order == expected, (name, order)
    records.append({"case": name, "ME_index_order": order})

handoffs = []
module = stock / "lib/modules/pf_rt_fc.ko"


def callback(name, args):
    if name == "rt_gpon_ponQueue_set":
        config = struct.unpack("<4I", oracle.uc.mem_read(args[2], 16))
        handoffs.append({"tcont": args[0], "queue": args[1], "type": config[0], "weight": config[3]})
        return 0
    if name == "printk":
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ["pf_rt_SetPriQueue"], callback, {"gDrvCtrl": 0x5004000})
for queue, weighted, weight in ((0, 0, 1), (2, 0, 1), (0, 1, 9), (1, 1, 9), (7, 0, 1)):
    data = struct.pack("<HHIIH2xIB3xI", queue, 1, 0, 0, weight, weighted, 0, 1)
    oracle.uc.mem_write(oracle.scratch, data)
    assert oracle.call("pf_rt_SetPriQueue", oracle.scratch) == 0
    assert handoffs[-1] == {"tcont": 1, "queue": queue, "type": weighted, "weight": weight}
report = {"original_comparator": "libomci_mib.so:0x19aa8",
          "queue_rank_write": "libomci_mib.so:0x1c3c8",
          "driver_handoff": "pf_rt_fc.ko:pf_rt_SetPriQueue",
          "original_sha256": {str(path.relative_to(stock)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (library, module)},
          "hardware_test": False, "RTK_backend_modelled": True,
          "sorting": records, "unchanged_queue_rank_handoffs": handoffs}
(root / "logs/stock-omci-queue-contract.json").write_text(json.dumps(report, indent=2) + "\n")
print("Original OMCI queue comparator: WRR first, SP priority descending, ME index ascending; five RTK handoffs retain the queue rank")
