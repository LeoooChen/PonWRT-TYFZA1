#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
umask 022
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TREE=${1:?Usage: sync-overlay.sh /path/to/openwrt-checkout}
PIN=79bc56cb7483bc716da07c369530b792631904b4
test "$(git -C "$TREE" rev-parse HEAD)" = "$PIN" || {
    echo "OpenWrt HEAD differs from sources.lock.json" >&2
    exit 1
}
mkdir -p "$TREE/target/linux/rtl9617c"
cp -a "$PORT/overlay/target/linux/rtl9617c/." "$TREE/target/linux/rtl9617c/"
cp "$PORT/configs/tyfza1-boot.config" "$TREE/.config"
echo "Applied RTL9617C overlay to $TREE at $PIN"
