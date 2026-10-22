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
# Embed the PE built from this source, never a stale generated overlay header.
PE_RADIO_CFLAGS=-DPE_UPLINK sh "$P/scripts/build_pe_radio_wd.sh" "$OUT/pe"
cp "$OUT/pe/pe-tx-code.h" "$OUT/pe-tx-code.h"
# A separate PCI driver name allows replacing the built-in driver after
# unbind. Every hardware/control function is compiled from the overlay.
python3 - "$OUT/pci.c" "$DRIVER" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace('.name = "rtl_ax_ap",', '.name = "' + sys.argv[2] + '",'))
PY
python3 - "$OVERLAY/Makefile" "$OUT/Makefile" "$DRIVER" <<'PYMAKE'
from pathlib import Path
import sys
name = sys.argv[3]
source = Path(sys.argv[1]).read_text()
Path(sys.argv[2]).write_text(source.replace('obj-$(CONFIG_RTL_AX_AP) += rtl_ax_ap.o',
    f'obj-m := {name}.o').replace('rtl_ax_ap-y :=', f'{name}-y :='))
PYMAKE
TOOLCHAIN=$(dirname "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin/aarch64-openwrt-linux-musl-gcc)
export PATH="$TOOLCHAIN:$PATH"
export STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- M="$OUT" modules
sha256sum "$OUT/$DRIVER.ko"
