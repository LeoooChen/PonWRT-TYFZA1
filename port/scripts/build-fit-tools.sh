#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# OpenWrt disables TOOLS_LIBCRYPTO in its normal mkimage. Build the same
# upstream host utilities with crypto enabled, without altering that build.
set -eu
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export LC_ALL=C
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TREE=$(realpath "${1:?Usage: build-fit-tools.sh openwrt-tree new-output-dir}")
OUT=${2:?Specify a new output directory}
[ ! -e "$OUT" ] || { echo "Output already exists: $OUT" >&2; exit 1; }
test "$(git -C "$TREE" rev-parse HEAD)" = "$(python3 "$PORT/tools/project_paths.py" --field openwrt-commit)"
VERSION=$(python3 "$PORT/tools/project_paths.py" --field uboot-version)
ARCHIVE=$TREE/dl/u-boot-$VERSION.tar.bz2
printf '%s  %s\n' "$(python3 "$PORT/tools/project_paths.py" --field uboot-sha256)" "$ARCHIVE" | sha256sum -c -
mkdir -p "$OUT/source" "$OUT/bin"
OUT=$(realpath "$OUT")
tar -xjf "$ARCHIVE" --strip-components=1 -C "$OUT/source"
make -C "$OUT/source" tools-only_defconfig
"$OUT/source/scripts/config" --file "$OUT/source/.config" --enable TOOLS_LIBCRYPTO --disable TOOLS_MKEFICAPSULE --disable TOOLS_KWBIMAGE
make -C "$OUT/source" olddefconfig
grep -q '^CONFIG_TOOLS_LIBCRYPTO=y' "$OUT/source/.config"
make -C "$OUT/source" -j2 tools-only
for tool in mkimage dumpimage fit_check_sign; do
    install -m 755 "$OUT/source/tools/$tool" "$OUT/bin/$tool"
done
cp "$OUT/source/.config" "$OUT/bin/uboot-tools.config"
"$OUT/bin/mkimage" -V
echo "Crypto-enabled host FIT tools: $OUT/bin"
