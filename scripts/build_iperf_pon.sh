#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:-$P/artifacts/iperf-pon}
mkdir -p "$OUT"
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
cp "$P"/tools/iperf-pon/*.c "$OUT/"
printf 'obj-m := iperf_pon_flow.o iperf_pon_feed.o iperf_pon_sink.o\nccflags-y += -I%s/port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek\n' "$P" > "$OUT/Makefile"
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH" STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- -j2 modules
aarch64-openwrt-linux-musl-gcc -O2 "$P/tools/iperf-pon/set_peer.c" -o "$OUT/set_peer"
aarch64-openwrt-linux-musl-gcc -O2 "$P/tools/iperf-pon/queue.c" -o "$OUT/queue"
