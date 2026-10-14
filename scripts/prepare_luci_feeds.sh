#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TREE=${1:-$P/sources/openwrt}
cp "$P/configs/feeds.conf" "$TREE/feeds.conf"
cd "$TREE"
if [ -n "${TYFZA1_PROXY:-}" ]; then
    export http_proxy=$TYFZA1_PROXY https_proxy=$TYFZA1_PROXY
fi
./scripts/feeds update packages luci
./scripts/feeds install -p packages cgi-io luasrcdiet
./scripts/feeds install -p luci luci
