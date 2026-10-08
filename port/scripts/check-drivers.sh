#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Compile against the same the locked kernel source; no hardware or emulation is run.
set -eu
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SRC=${1:?Usage: check-drivers.sh /path/to/linux-the locked kernel /path/to/output}
OUT=${2:?Specify a separate kernel output directory}
OVERLAY=$PORT/overlay/target/linux/rtl9617c
for diff in "$OVERLAY"/patches-6.18/*.patch; do
    if patch -d "$SRC" -p1 --forward --batch --dry-run < "$diff" >/dev/null 2>&1; then
        patch -d "$SRC" -p1 --forward --batch < "$diff"
    elif ! patch -d "$SRC" -p1 --reverse --batch --dry-run < "$diff" >/dev/null 2>&1; then
        echo "Source does not match the pinned patch: $diff" >&2
        exit 1
    fi
done
cp -a "$OVERLAY/files-6.18/." "$SRC/"
make -C "$SRC" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- tinyconfig
"$SRC/scripts/config" --file "$OUT/.config" \
    --enable OF --enable ARCH_RTL9617C --enable TTY --enable PRINTK \
    --enable SERIAL_RTL9617C --enable SERIAL_RTL9617C_CONSOLE \
    --enable SPI --enable SPI_MASTER --enable SPI_RTL9617C \
    --enable MTD --enable MTD_SPI_NAND \
    --enable RTL9617C_PER_IRQ --enable WATCHDOG --enable RTL9617C_WATCHDOG \
    --enable NET --enable NETDEVICES --enable PHYLIB --enable MDIO_RTL9617C
make -C "$SRC" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make -C "$SRC" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j8 \
    drivers/tty/serial/rtl9617c-uart.o drivers/spi/spi-rtl9617c.o \
    drivers/mtd/nand/spi/core.o drivers/irqchip/irq-rtl9617c-per.o \
    drivers/watchdog/rtl9617c-wdt.o drivers/net/mdio/mdio-rtl9617c.o
