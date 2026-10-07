#!/usr/bin/env python3
"""Compare the native bring-up DTB with the fixed TYFZA1 backup, using fdtget."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

STOCK_SHA256 = "5124f860df3bcd2e20b87d058e19b1ddfdc7a46c9d876663ba9feeff5a0f13ac"


def get(path, node, prop, kind="x"):
    result = subprocess.run(
        ["fdtget", "-t", kind, str(path), node, prop],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return result.split() if kind == "s" else [int(v, 16) for v in result.split()]


def children(path, node):
    return subprocess.run(
        ["fdtget", "-l", str(path), node],
        check=True, capture_output=True, text=True,
    ).stdout.split()


def reservations(path):
    return sorted(get(path, "/reserved-memory/" + n, "reg")
                  for n in children(path, "/reserved-memory"))


def inspect(stock, native):
    if hashlib.sha256(stock.read_bytes()).hexdigest() != STOCK_SHA256:
        raise ValueError("Wrong stock DTB: expected the identified TYFZA1 backup")
    pairs = [
        ("RAM", "/memory", "/memory@0", "reg"),
        ("GIC", "/interrupt-controller@4f8000000", "/interrupt-controller@4f8000000", "reg"),
        ("UART registers", "/uart@f43291b0", "/soc/serial@f43291b0", "reg"),
        ("UART interrupt", "/uart@f43291b0", "/soc/serial@f43291b0", "interrupts"),
        ("UART clock", "/g3_apb_pclk", "/clock-apb", "clock-frequency"),
        ("Timer frequency", "/timer", "/timer", "clock-frequency"),
        ("QSPI registers", "/sf-ctrl@f4324000", "/soc/spi@4f4324000", "reg"),
        ("Peripheral IRQ registers", "/per_ictl@f4329004", "/soc/interrupt-controller@f4329004", "reg"),
        ("Peripheral IRQ parent", "/per_ictl@f4329004", "/soc/interrupt-controller@f4329004", "interrupts"),
        ("Watchdog registers", "/wdt@f432901c", "/soc/watchdog@f432901c", "reg"),
        ("Watchdog interrupt", "/wdt@f432901c", "/soc/watchdog@f432901c", "interrupts"),
        ("Watchdog reset delay", "/wdt@f432901c", "/soc/watchdog@f432901c", "delay-reset"),
        ("Watchdog reset mode", "/wdt@f432901c", "/soc/watchdog@f432901c", "reset-on-timeout"),
    ]
    checked = []
    for label, old_node, new_node, prop in pairs:
        old, new = get(stock, old_node, prop), get(native, new_node, prop)
        if old != new:
            raise ValueError(f"{label} differs: {old!r} != {new!r}")
        checked.append(label)
    for n in ("cpu@0", "cpu@100", "cpu@200", "cpu@300"):
        if get(stock, "/cpus/" + n, "reg") != get(native, "/cpus/" + n, "reg"):
            raise ValueError(f"MPIDR differs: {n}")
    stock_ni = get(stock, "/ethernet@f4300000", "reg")
    # These windows belong to the PHY bus, not the NI DMA datapath.
    mdio_windows = [word for index in (2, 11, 15) for word in stock_ni[index * 4:index * 4 + 4]]
    if get(native, "/soc/mdio@4f4329118", "reg") != mdio_windows:
        raise ValueError("MDIO/OCP/wrapper windows differ from stock NI resources")
    checked.append("MDIO/OCP/wrapper registers")
    if reservations(stock) != reservations(native):
        raise ValueError("Stock reserved memory ranges changed")
    if get(native, "/psci", "method", "s") != get(stock, "/psci", "method", "s"):
        raise ValueError("PSCI method changed")
    if "realtek,rtl9617c" not in get(native, "/", "compatible", "s"):
        raise ValueError("Missing RTL9617C compatible")
    return {
        "native_sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
        "stock_sha256": STOCK_SHA256,
        "checked": checked + ["MPIDRs", "reserved-memory", "PSCI"],
        "status": "offline agreement with TYFZA1 backup; no live boot/ECC validation",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path)
    parser.add_argument("native", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.stock, args.native), indent=2))
