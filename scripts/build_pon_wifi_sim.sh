#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=$P/artifacts/live-pon-wifi-sim
mkdir -p "$OUT"
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
cp "$P/tools/live-pon-diag/pon_wifi_sim.c" "$P/tools/live-pon-diag/pon_sim_send.c" "$OUT/"
printf 'obj-m := pon_wifi_sim.o pon_sim_send.o\n' > "$OUT/Makefile"
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH" STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- -j2 modules
