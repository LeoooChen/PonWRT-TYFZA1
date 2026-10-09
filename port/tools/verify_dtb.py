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
        if label == "Watchdog registers" and len(new) == 4:
            if old[:4] != new or old[4:8] != get(native, "/soc/syscon@f43200c0", "reg"):
                raise ValueError("Watchdog timer/shared global config differs from stock")
            if get(native, new_node, "realtek,global-config") != get(native, "/soc/syscon@f43200c0", "phandle"):
                raise ValueError("Watchdog shared global config supplier differs")
            checked.append(label + " with shared syscon")
            continue
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
    nodes = children(native, "/soc")
    if "i2c@4f4329160" in nodes:
        old = get(stock, "/i2c@f4329160", "reg")
        new = get(native, "/soc/i2c@4f4329160", "reg")
        if new != [4, old[1], old[2], old[3]]:
            raise ValueError("Native BIW I2C window differs from the stock controller")
        if get(native, "/soc/i2c@4f4329160", "clock-frequency") != get(stock, "/i2c@f4329160", "clock-frequency"):
            raise ValueError("BIW I2C bus frequency differs from stock")
        checked.append("BIW I2C registers/bus frequency")
    if "ethernet@4f4300000" in nodes:
        windows = get(native, "/soc/ethernet@4f4300000", "reg")
        names = get(native, "/soc/ethernet@4f4300000", "reg-names", "s")
        for name, index in (("ni", 0), ("xram", 3), ("dma-lso", 4), ("qm-axi-reorder", 10)):
            if name not in names:
                continue
            pos = names.index(name) * 4
            expected = stock_ni[index * 4:index * 4 + 4]
            actual = windows[pos:pos + 4]
            if actual[:3] != expected[:3] or actual[3] > expected[3]:
                raise ValueError(f"Native {name} window differs from stock NI resource")
            checked.append(f"NI {name} registers")
    if "pon@4f5500000" in nodes:
        pon = get(native, "/soc/pon@4f5500000", "reg")
        if pon[:4] != stock_ni[8 * 4:8 * 4 + 4]:
            raise ValueError("PON MAC window differs from stock NI resource8")
        if pon[4:] != [4, 0xf4320094, 0, 4, 4, 0xf4320074, 0, 4, 4, 0xf432022c, 0, 4,
                       4, 0xf4320080, 0, 4, 4, 0xf4320090, 0, 4, 4, 0xf4320180, 0, 8, 4, 0xf432007c, 0, 4]:
            raise ValueError("PON global control window differs from register definition")
        checked.append("PON MAC/global registers")
        supplier = get(native, "/soc/pon@4f5500000", "realtek,network-engine")
        if supplier != get(native, "/soc/ethernet@4f4300000", "phandle"):
            raise ValueError("PON network engine supplier differs from the native NI device")
        checked.append("PON/NI supplier binding")
        bosa_node = "/soc/i2c@4f4329160/optical-front-end@51"
        if get(native, "/soc/pon@4f5500000", "realtek,optical-front-end") != get(native, bosa_node, "phandle"):
            raise ValueError("PON optical front-end supplier differs from the BOSA I2C client")
        if get(native, bosa_node, "reg") != [0x51]:
            raise ValueError("BOSA address differs from the original rtkbosa bus access")
        if get(native, "/soc/pinctrl@4f43200f0/i2c0-default/data-signals", "pins", "s") != get(stock, "/pinctrl@0xf43200f0/i2c0_default/data_signals", "pins", "s"):
            raise ValueError("I2C pad routing differs from the stock board")
        checked.append("PON/BOSA supplier and I2C pad routing")
        for prop in ("reg", "interrupts"):
            if get(native, "/soc/interrupt-controller@f4320164", prop) != get(stock, "/ne_ictl@0xf4320164", prop):
                raise ValueError("NE/PON interrupt topology differs from stock")
        if get(native, "/soc/pon@4f5500000", "interrupts") != get(stock, "/ethernet@f4300000/pon_ictl@0", "interrupts"):
            raise ValueError("PON interrupt bank bit differs from stock")
        checked.append("NE/PON interrupt topology")
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
