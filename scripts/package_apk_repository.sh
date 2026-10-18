#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
TREE=$(realpath "${1:?Usage: package_apk_repository.sh openwrt-tree output-directory}")
OUT=${2:?Usage: package_apk_repository.sh openwrt-tree output-directory}
mkdir -p "$OUT"
OUT=$(realpath "$OUT")
# Use the same merged package set that OpenWrt used to assemble the image.
rm -f "$OUT"/*.apk
cp -L "$TREE"/staging_dir/packages/rtl9617c/*.apk "$OUT/"
cp "$TREE/public-key.pem" "$OUT/"
"$TREE/staging_dir/host/bin/apk" mkndx \
    --keys-dir "$TREE" --sign "$TREE/private-key.pem" \
    --output "$OUT/packages.adb" "$OUT"/*.apk
