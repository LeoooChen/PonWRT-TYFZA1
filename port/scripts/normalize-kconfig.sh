#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Maintainer operation after changing native kernel options, not a boot test.
set -eu
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec 9>"$PORT/../artifacts/openwrt-world.lock"
flock 9
TREE=${1:?Usage: normalize-kconfig.sh /path/to/prepared/openwrt-checkout}
KERNEL=$(python3 "$PORT/tools/project_paths.py" --kernel-directory "$TREE")
test -f "$KERNEL/.config.set"
set -- "$TREE"/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
test "$#" = 1 && test -d "$1"
export PATH="$1:$TREE/staging_dir/host/bin:$PATH"
cp "$KERNEL/.config.set" "$KERNEL/.config"
make -C "$KERNEL" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- olddefconfig
"$TREE/scripts/kconfig.pl" '>' "$KERNEL/.config.set" "$KERNEL/.config" > "$TREE/tmp/rtl9617c-kconfig-closure"
"$TREE/scripts/kconfig.pl" '+' "$PORT/overlay/target/linux/rtl9617c/config-6.18" \
    "$TREE/tmp/rtl9617c-kconfig-closure" > "$TREE/tmp/rtl9617c-config-closed"
cp "$TREE/tmp/rtl9617c-config-closed" "$PORT/overlay/target/linux/rtl9617c/config-6.18"
cp "$TREE/tmp/rtl9617c-config-closed" "$TREE/target/linux/rtl9617c/config-6.18"
# Kernel packages embed the configuration ABI hash; refresh them on the next
# world build along with the normalized kernel, rather than retaining old APKs.
make -C "$TREE" package/kernel/linux/clean
echo "Closed native Kconfig; re-run the build to validate this target config."
