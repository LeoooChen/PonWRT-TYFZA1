#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Full kernel builds regenerate Module.symvers; compile diagnostics after them.
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH"
export STAGING_DIR="$P/sources/openwrt/staging_dir/target-aarch64_cortex-a55_musl"
make -C "$KERNEL" M="$P/tools/live-pon-diag" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- -j2 modules
sha256sum "$P/tools/live-pon-diag/live_fe_mask.ko" "$P/tools/live-pon-diag/live_fe_memory.ko" > "$P/artifacts/live-fe-modules.sha256"
