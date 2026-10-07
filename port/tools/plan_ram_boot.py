#!/usr/bin/env python3
"""Check measured RAM addresses and print a volatile booti plan; never flash."""
import argparse
import re
import struct
import subprocess
from pathlib import Path


def fdt(path, node, prop):
    output = subprocess.run(["fdtget", "-t", "x", str(path), node, prop],
                            check=True, capture_output=True, text=True).stdout
    return [int(v, 16) for v in output.split()]


def nodes(path, node):
    return subprocess.run(["fdtget", "-l", str(path), node], check=True,
                          capture_output=True, text=True).stdout.split()


def number(value):
    return int(value, 0)


def interval(start, size):
    if start < 0 or size <= 0 or start + size > 1 << 64:
        raise ValueError("Invalid or overflowing memory interval")
    return start, start + size


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def reg_intervals(words):
    if len(words) % 4:
        raise ValueError("Require verified 2-cell addresses and 2-cell sizes")
    return [interval((words[i] << 32) | words[i + 1],
                     (words[i + 2] << 32) | words[i + 3])
            for i in range(0, len(words), 4)]


def validate(memory, reserved, kernel_addr, kernel_size, text_offset, dtb_addr, dtb_size):
    if kernel_addr < text_offset or (kernel_addr - text_offset) % 0x200000:
        raise ValueError("Image placement violates ARM64 2 MiB alignment/text_offset")
    if dtb_addr % 8 or dtb_size > 0x200000:
        raise ValueError("DTB must be 8-byte aligned and at most 2 MiB")
    kernel, dtb = interval(kernel_addr, kernel_size), interval(dtb_addr, dtb_size)
    for name, region in (("kernel", kernel), ("DTB", dtb)):
        if not any(bank[0] <= region[0] and region[1] <= bank[1] for bank in memory):
            raise ValueError(f"{name} lies outside measured RAM")
        if any(overlap(region, hole) for hole in reserved):
            raise ValueError(f"{name} overlaps reserved/bootloader memory")
    if overlap(kernel, dtb):
        raise ValueError("Kernel and DTB overlap")


def plan(args):
    if not args.booti_confirmed:
        raise ValueError("First confirm booti support using U-Boot help booti")
    if "initramfs" not in args.image.name:
        raise ValueError("Use the OpenWrt initramfs Image for the first RAM boot")
    for path in (args.image, args.dtb):
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", path.name):
            raise ValueError("Artifact filenames contain characters unsafe in U-Boot commands")
    if fdt(args.live_dtb, "/", "#address-cells") != [2] or \
       fdt(args.live_dtb, "/", "#size-cells") != [2]:
        raise ValueError("Live DT has unsupported memory cell geometry")
    memory = []
    for node in nodes(args.live_dtb, "/"):
        if node == "memory" or node.startswith("memory@"):
            memory.extend(reg_intervals(fdt(args.live_dtb, "/" + node, "reg")))
    reserved = []
    for node in nodes(args.live_dtb, "/reserved-memory"):
        reserved.extend(reg_intervals(fdt(args.live_dtb, "/reserved-memory/" + node, "reg")))
    for value in args.uboot_range:
        start, size = value.split(":")
        reserved.append(interval(number(start), number(size)))
    with args.image.open("rb") as source:
        header = source.read(64)
    if len(header) != 64 or header[56:60] != b"ARM\x64":
        raise ValueError("Input is not an uncompressed ARM64 Image")
    text_offset, image_size = struct.unpack_from("<QQ", header, 8)
    if not image_size:
        raise ValueError("Image header has no image_size")
    validate(memory, reserved, args.kernel_address, max(image_size, args.image.stat().st_size),
             text_offset, args.dtb_address, args.dtb.stat().st_size)
    commands = ["# Volatile RAM plan; addresses supplied from live evidence, no saveenv."]
    for address, path in ((args.kernel_address, args.image), (args.dtb_address, args.dtb)):
        if args.transport == "tftp":
            commands.append(f"tftpboot {address:#x} {path.name}")
        else:
            commands.append(f"loady {address:#x}")
            commands.append(f"# Send {path.name} using the terminal's YMODEM transfer.")
    commands.extend([
        "setenv bootargs 'earlycon console=ttyS0,115200n8 loglevel=8 ignore_loglevel clk_ignore_unused maxcpus=1'",
        f"booti {args.kernel_address:#x} - {args.dtb_address:#x}",
    ])
    return "\n".join(commands)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("--live-dtb", type=Path, required=True)
    parser.add_argument("--kernel-address", type=number, required=True)
    parser.add_argument("--dtb-address", type=number, required=True)
    parser.add_argument("--uboot-range", action="append", required=True,
                        help="start:size of bootloader/stack/malloc areas from bdinfo; repeat as needed")
    parser.add_argument("--booti-confirmed", action="store_true")
    parser.add_argument("--transport", choices=("tftp", "ymodem"), default="tftp")
    args = parser.parse_args()
    try:
        print(plan(args))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Cannot prepare RAM boot: {error}\n")
