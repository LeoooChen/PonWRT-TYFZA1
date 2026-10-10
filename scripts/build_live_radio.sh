#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Build the current native radio sources for RAM-only board iteration.
set -euo pipefail
P=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
OUT=${1:-$P/artifacts/live-radio}
DRIVER=${2:-rtl_ax_ap_live}
cd "$P"
exec 9>artifacts/openwrt-world.lock
flock 9
KERNEL=$(python3 port/tools/project_paths.py --kernel-directory "$P/sources/openwrt")
OVERLAY=$P/port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap
mkdir -p "$OUT"
cp "$OVERLAY"/*.[ch] "$OUT/"
# A separate PCI driver name allows replacing the built-in driver after
# unbind. Every hardware/control function is compiled from the overlay.
python3 - "$OUT/pci.c" "$DRIVER" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace('.name = "rtl_ax_ap",', '.name = "' + sys.argv[2] + '",'))
PY
python3 - "$OUT/Makefile" "$DRIVER" <<'PYMAKE'
from pathlib import Path
import sys
name = sys.argv[2]
Path(sys.argv[1]).write_text(f"obj-m := {name}.o\n{name}-y := pci.o phy.o rx.o tx.o power-tables.o limits.o\n")
PYMAKE
TOOLCHAIN=$(dirname "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin/aarch64-openwrt-linux-musl-gcc)
export PATH="$TOOLCHAIN:$PATH"
export STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- M="$OUT" modules
sha256sum "$OUT/$DRIVER.ko"
