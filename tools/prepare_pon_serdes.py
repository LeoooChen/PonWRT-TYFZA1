#!/usr/bin/env python3
"""Convert TYFZA1 stock SerDes tables into native register firmware records."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("stock_directory", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
manifest = {}
sources = sorted(args.stock_directory.glob("pon_serdes_*.txt"))
if not sources:
    raise ValueError(f"No original SerDes profiles: {args.stock_directory}")
for source in sources:
    records = []
    for line in source.read_text().splitlines():
        if not line.strip().startswith("0x"):
            continue
        address, value, readback, delay = [int(word, 0) for word in line.split()]
        offset = address & 0xffff
        if offset >= 0xc000 or offset % 4:
            raise ValueError(f"PON register outside MAC resource: {address:#x}")
        records.append((offset, value, delay or 10))
    # The verified stock loader caps tables at 512 rows and uses value column1.
    if not records or len(records) > 512:
        raise ValueError(f"Table exceeds the verified stock loader: {source.name}")
    firmware = struct.pack("<4sII", b"TPON", 1, len(records))
    firmware += b"".join(struct.pack("<III", *record) for record in records)
    destination = args.output / (source.stem + ".bin")
    destination.write_bytes(firmware)
    manifest[source.name] = {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "firmware_sha256": hashlib.sha256(firmware).hexdigest(),
        "firmware": destination.name,
        "records": len(records),
        "total_delay_us": sum(record[2] for record in records),
    }
(args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"profiles": len(manifest), "output": str(args.output)}, indent=2))
