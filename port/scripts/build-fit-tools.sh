#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# OpenWrt disables TOOLS_LIBCRYPTO in its normal mkimage. Build the same
# upstream host utilities with crypto enabled, without altering that build.
set -eu
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export LC_ALL=C
TREE=$(realpath "${1:?Usage: build-fit-tools.sh openwrt-tree new-output-dir}")
OUT=${2:?Specify a new output directory}
[ ! -e "$OUT" ] || { echo "Output already exists: $OUT" >&2; exit 1; }
test "$(git -C "$TREE" rev-parse HEAD)" = 79bc56cb7483bc716da07c369530b792631904b4
ARCHIVE=$TREE/dl/u-boot-2026.07.tar.bz2
printf '%s  %s\n' 78e8bfc382fe388f9b55aa1daf8c563522a037779b5d4c349d1415e381f1243e "$ARCHIVE" | sha256sum -c -
mkdir -p "$OUT/source" "$OUT/bin"
OUT=$(realpath "$OUT")
tar -xjf "$ARCHIVE" --strip-components=1 -C "$OUT/source"
make -C "$OUT/source" tools-only_defconfig
"$OUT/source/scripts/config" --file "$OUT/source/.config" --enable TOOLS_LIBCRYPTO --disable TOOLS_MKEFICAPSULE --disable TOOLS_KWBIMAGE
make -C "$OUT/source" olddefconfig
grep -q '^CONFIG_TOOLS_LIBCRYPTO=y' "$OUT/source/.config"
make -C "$OUT/source" -j8 tools-only
for tool in mkimage dumpimage fit_check_sign; do
    install -m 755 "$OUT/source/tools/$tool" "$OUT/bin/$tool"
done
cp "$OUT/source/.config" "$OUT/bin/uboot-tools.config"
"$OUT/bin/mkimage" -V
echo "Crypto-enabled host FIT tools: $OUT/bin"
