#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
KERNEL=${1:?Usage: check-dtb.sh /path/to/linux-source /path/to/output /path/to/stock.dtb}
OUT=${2:?Specify an output directory}
STOCK=${3:?Specify the identified TYFZA1 backup DTB}
mkdir -p "$OUT"
for name in rtl9617c-tyfza1 rtl9617c-tyfza1-nand-readonly rtl9617c-tyfza1-stock-ubi-readonly rtl9617c-tyfza1-mdio rtl9617c-tyfza1-xram-lan rtl9617c-tyfza1-dma-tx rtl9617c-tyfza1-ni; do
    gcc -E -P -x assembler-with-cpp -nostdinc -undef -D__DTS__ \
        -I "$KERNEL/include" "$PORT/overlay/target/linux/rtl9617c/dts/$name.dts" \
        -o "$OUT/$name.dts"
    dtc -I dts -O dtb -o "$OUT/$name.dtb" "$OUT/$name.dts"
    python3 "$PORT/tools/verify_dtb.py" "$STOCK" "$OUT/$name.dtb"
done
