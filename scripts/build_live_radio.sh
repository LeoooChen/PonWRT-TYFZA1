#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Build the current native radio sources for RAM-only board iteration.
set -euo pipefail
P=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
OUT=${1:-$P/artifacts/live-radio}
cd "$P"
exec 9>artifacts/openwrt-world.lock
flock 9
KERNEL=$(python3 port/tools/project_paths.py --kernel-directory "$P/sources/openwrt")
OVERLAY=$P/port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap
mkdir -p "$OUT"
cp "$OVERLAY"/*.[ch] "$OUT/"
# A separate PCI driver name allows replacing the built-in driver after
# unbind. Every hardware/control function is compiled from the overlay.
python3 - "$OUT/pci.c" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace('.name = "rtl_ax_ap",', '.name = "rtl_ax_ap_live",'))
PY
cat > "$OUT/Makefile" <<'MAKE'
obj-m := rtl_ax_ap_live.o
rtl_ax_ap_live-y := pci.o phy.o rx.o power-tables.o
MAKE
TOOLCHAIN=$(dirname "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin/aarch64-openwrt-linux-musl-gcc)
export PATH="$TOOLCHAIN:$PATH"
export STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- M="$OUT" modules
sha256sum "$OUT/rtl_ax_ap_live.ko"
