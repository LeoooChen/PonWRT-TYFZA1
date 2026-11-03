#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build only; run the isolated module explicitly on a matching kernel.
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:-$P/artifacts/udp-gro-semantics-test}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
cp "$P/tools/test_udp_gro_semantics.c" "$OUT/"
printf 'obj-m := test_udp_gro_semantics.o\n' > "$OUT/Makefile"
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH" STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- modules
