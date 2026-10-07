#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Signing keys and crypto-enabled host tools must be supplied explicitly.
set -eu
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TREE=$(realpath "${1:?Usage: build-boot-fit.sh openwrt-tree crypto-tools-bin key-dir key-dtb new-output-dir}")
TOOLS=$(realpath "${2:?Specify the build-fit-tools.sh bin directory}")
KEYDIR=$(realpath "${3:?Specify the validated dev.key/dev.crt directory}")
KEYDTB=$(realpath "${4:?Specify the actual bootloader public-key DTB}")
OUT=${5:?Specify a new output directory}
[ ! -e "$OUT" ] || { echo "Output already exists: $OUT" >&2; exit 1; }
test "$(git -C "$TREE" rev-parse HEAD)" = 79bc56cb7483bc716da07c369530b792631904b4
grep -q '^CONFIG_TOOLS_LIBCRYPTO=y' "$TOOLS/uboot-tools.config"
for tool in mkimage dumpimage fit_check_sign; do
    test -x "$TOOLS/$tool"
done
export PATH="$TOOLS:$TREE/staging_dir/host/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
BIN=$TREE/bin/targets/rtl9617c/generic
PREFIX=$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1
set -- "$BIN"/*-initramfs-Image
test "$#" = 1 && test -f "$1"
python3 "$PORT/tools/make_boot_fit.py" "$1" "$PREFIX-board.dtb" "$OUT/ram" --mode ram --key-dir "$KEYDIR" --key-dtb "$KEYDTB"
python3 "$PORT/tools/make_boot_fit.py" "$PREFIX-Image" "$PREFIX-stock-ubi-readonly.dtb" "$OUT/ubi" --mode ubi --rootfs "$PREFIX-squashfs-rootfs.squashfs" --key-dir "$KEYDIR" --key-dtb "$KEYDTB"
echo "Signed candidate verified offline. Live boot and installation remain unverified."
