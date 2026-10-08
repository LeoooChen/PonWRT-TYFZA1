#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=$P/artifacts/live-pon-modules
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
SOURCE=$P/port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek
mkdir -p "$OUT"
cp "$SOURCE"/rtl9617c-pon*.c "$SOURCE"/rtl9617c-*.h "$OUT/"
cp "$P/port/overlay/target/linux/rtl9617c/files-6.18/drivers/gpio/gpio-rtl9617c.c" "$OUT/"
cp "$P/tools/live-pon-diag/live_bindings.c" "$OUT/"
python3 - "$OUT/rtl9617c-pon.c" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text().replace('.name = "rtl9617c-pon",', '.name = "rtl9617c-pon-live",')
p.write_text(s)
PY
cat > "$OUT/Makefile" <<'MAKE'
obj-m := tyfza1_pon_live.o gpio-rtl9617c.o live_bindings.o
tyfza1_pon_live-y := rtl9617c-pon.o rtl9617c-pon-serdes.o rtl9617c-pon-epon.o rtl9617c-pon-mpcp.o rtl9617c-pon-pdc.o rtl9617c-pon-puc.o rtl9617c-pon-oam.o
MAKE
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH"
export STAGING_DIR="$P/sources/openwrt/staging_dir/target-aarch64_cortex-a55_musl"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- -j2 modules
sha256sum "$OUT/"*.ko > "$OUT/SHA256SUMS"
