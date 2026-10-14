#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
OUT=$P/artifacts/live-wifi-lookup-probe
mkdir -p "$OUT"
cp "$P/tools/live_wifi_lookup_probe.c" "$OUT/"
printf 'obj-m := live_wifi_lookup_probe.o\nccflags-y += -I%s/port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek\n' "$P" > "$OUT/Makefile"
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH" STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- modules
