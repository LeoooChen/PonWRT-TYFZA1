#!/usr/bin/env python3
"""Inventory the TYFZA1 eponoamd callback tables without executing the daemon."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
data = args.binary.read_bytes()
expected = "a9b075cdee5a2b176eca35e731c7b0d48881d46091d7b7469344e99c7f2e4b28"
digest = hashlib.sha256(data).hexdigest()
if digest != expected:
    raise SystemExit("This address map belongs to the recorded TYFZA1 stock eponoamd only")
loads = []
phoff = struct.unpack_from("<Q", data, 32)[0]
phsize, phcount = struct.unpack_from("<HH", data, 54)
for i in range(phcount):
    kind, flags, offset, address, physical, size, memory, align = struct.unpack_from(
        "<IIQQQQQQ", data, phoff + i * phsize)
    if kind == 1:
        loads.append((address, offset, size))


def read(address, size):
    for base, offset, length in loads:
        if base <= address and address + size <= base + length:
            start = address - base + offset
            return data[start:start + size]
    raise ValueError(hex(address))


tables = {"standard_attributes": 0x477818, "standard_actions": 0x477ab8,
          "extended_default": 0x477c38, "extended_flag_0x20000000": 0x478c58,
          "extended_actions": 0x479eb8}
records = []
for table, address in tables.items():
    while True:
        record = read(address, 96)
        if not record[0]:
            break
        branch, padding, leaf, operations, target, reserved, getter, setter = struct.unpack_from(
            "<BBHBBHQQ", record)
        records.append({"table": table, "address": hex(address), "branch": f"{branch:02x}",
            "leaf": f"{leaf:04x}", "operations": operations, "target": target,
            "getter": hex(getter) if getter else None, "setter": hex(setter) if setter else None,
            "name": record[24:].split(b"\0")[0].decode("ascii")})
        address += 96
callbacks = {row[operation] for row in records for operation in ("getter", "setter") if row[operation]}
args.output.write_text(json.dumps({"stock_sha256": digest, "table_lookup": "eponoamd:0x416e90",
    "selection": "extended table depends on runtime flag0x20000000; not inferred from negotiated CTC version",
    "record_bytes": 96, "records": records, "unique_callback_count": len(callbacks)}, indent=2) + "\n")
print(f"{len(records)} table entries, {len(callbacks)} distinct callback functions")
