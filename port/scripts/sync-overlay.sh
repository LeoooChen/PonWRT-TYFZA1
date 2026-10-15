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
mkdir -p "$TREE/package/luci-app-tyfza1-pon"
# Remove the retired overview views from incremental build trees.
rm -f "$TREE/package/luci-app-tyfza1-pon/htdocs/luci-static/resources/view/status/tyfza1.js" \
    "$TREE/package/luci-app-tyfza1-pon/htdocs/luci-static/resources/view/status/include/05_tyfza1.js"
cp -a "$PORT/packages/luci-app-tyfza1-pon/." "$TREE/package/luci-app-tyfza1-pon/"
mkdir -p "$TREE/package/tyfza1-pon-control"
cp -a "$PORT/packages/tyfza1-pon-control/." "$TREE/package/tyfza1-pon-control/"
if [ -n "${TYFZA1_CONFIG:-}" ]; then
    cp "$TYFZA1_CONFIG" "$TREE/.config"
elif [ ! -s "$TREE/.config" ]; then
    cp "$PORT/configs/tyfza1-boot.config" "$TREE/.config"
fi
echo "Applied RTL9617C overlay to $TREE at $PIN"
