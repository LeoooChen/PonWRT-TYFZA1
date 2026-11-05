#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TREE=${1:?Usage: apply-luci-patches.sh /path/to/openwrt-checkout}
TREE=$(realpath "$TREE")

for change in "$PORT"/luci-patches/*.patch; do
	if ! git -C "$TREE/feeds/luci" apply --reverse --check "$change" 2>/dev/null; then
		git -C "$TREE/feeds/luci" apply --check "$change"
		git -C "$TREE/feeds/luci" apply "$change"
	fi
done
