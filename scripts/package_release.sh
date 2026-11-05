#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TREE=${1:?Usage: package_release.sh openwrt-tree output-directory}
TREE=$(realpath "$TREE")
OUT=${2:?Usage: package_release.sh openwrt-tree output-directory}
mkdir -p "$OUT"
OUT=$(realpath "$OUT")
BIN=$TREE/bin/targets/rtl9617c/generic
PREFIX=$(make --no-print-directory -s -C "$TREE/target/linux/rtl9617c/image" \
    TOPDIR="$TREE" val.IMG_PREFIX)-tianyi_tyfza1
cp "$BIN/$PREFIX-kernel.itb" "$OUT/kernel.itb"
cp "$BIN/$PREFIX-squashfs-rootfs.squashfs" "$OUT/rootfs.squashfs"
cp "$BIN/$PREFIX-squashfs-sysupgrade.bin" "$OUT/sysupgrade.bin"
cp "$PROJECT/scripts/install_ubi_telnet.sh" "$OUT/install.sh"
cp "$PROJECT/port/overlay/target/linux/rtl9617c/base-files/lib/upgrade/tyfza1-storage.sh" "$OUT/"
cp "$PROJECT/port/overlay/target/linux/rtl9617c/base-files/etc/fw_env.config" "$OUT/fw_env.config"

VERSION=$(python3 "$PROJECT/port/tools/project_paths.py" --field uboot-version)
SOURCE=$PROJECT/artifacts/release-envtools/u-boot-$VERSION
mkdir -p "$PROJECT/artifacts/release-envtools"
tar -xf "$TREE/dl/u-boot-$VERSION.tar.bz2" -C "$PROJECT/artifacts/release-envtools"
set -- "$TREE"/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin/aarch64-openwrt-linux-musl-gcc
CC=$1
STRIP=${CC%gcc}strip
export STAGING_DIR=$TREE/staging_dir/target-aarch64_cortex-a55_musl
# The OEM shell can run this tool without OpenWrt's musl shared libraries.
set -- "$TREE"/build_dir/target-aarch64_cortex-a55_musl/mtd-utils-*
MTD=$1
"$CC" -Os -static -include "$MTD/include/config.h" -I"$MTD/include" \
    "$MTD/ubi-utils/ubirsvol.c" "$MTD/libmtd.a" "$MTD/libubi.a" -o "$OUT/ubirsvol"
"$STRIP" "$OUT/ubirsvol"
make -C "$SOURCE" tools-only_defconfig
make -C "$SOURCE" -j2 envtools CC="$CC" STRIP="$STRIP" \
    KBUILD_HOSTCFLAGS=-Os KBUILD_HOSTLDFLAGS=-static
cp "$SOURCE/tools/env/fw_printenv" "$OUT/fw_printenv"
"$STRIP" "$OUT/fw_printenv"
cp "$OUT/fw_printenv" "$OUT/fw_setenv"
tar --owner=0 --group=0 --numeric-owner -czf "$OUT/tyfza1-telnet-install.tar.gz" \
    -C "$OUT" kernel.itb rootfs.squashfs install.sh tyfza1-storage.sh ubirsvol fw_env.config fw_printenv fw_setenv
cp "$PROJECT/COPYING" "$OUT/COPYING"
(cd "$OUT" && sha256sum sysupgrade.bin tyfza1-telnet-install.tar.gz > SHA256SUMS)
