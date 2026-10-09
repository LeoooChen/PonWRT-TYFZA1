#!/bin/bash
set -euo pipefail
P=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
OUT=${1:-$P/artifacts/live-radio-rx-stage}
cd "$P"
exec 9>artifacts/openwrt-world.lock
flock 9
mkdir -p "$OUT"
cp "$P/port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/"*.[ch] "$OUT/"
python3 "$P/tools/wifi_rx_stage_observe.py" "$OUT/pci.c"
cat > "$OUT/Makefile" <<'MAKE'
obj-m := rtl_ax_ap_live.o
rtl_ax_ap_live-y := pci.o phy.o rx.o power-tables.o limits.o
MAKE
KERNEL=$(python3 port/tools/project_paths.py --kernel-directory "$P/sources/openwrt")
TOOLCHAIN=$(dirname "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin/aarch64-openwrt-linux-musl-gcc)
export PATH="$TOOLCHAIN:$PATH"
export STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- M="$OUT" modules
sha256sum "$OUT/rtl_ax_ap_live.ko"
