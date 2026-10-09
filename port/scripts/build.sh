#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export http_proxy=${TYFZA1_PROXY:-http://host.orb.internal:7897}
export https_proxy=$http_proxy
export CURL_OPTIONS='--speed-time 60 --speed-limit 1024'
umask 022
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PROJECT=$(CDPATH= cd -- "$PORT/.." && pwd)
TREE=${1:?Usage: build.sh /path/to/openwrt-checkout [jobs]}
TREE=$(realpath "$TREE")
JOBS=${2:-2}
python3 "$PROJECT/tools/prepare_pon_runtime.py" "$TREE/files" \
    --stock "${TYFZA1_STOCK_INPUTS:-$PROJECT/analysis/private/live-files}" \
    --auth-config "${TYFZA1_AUTH_CONFIG:-$PROJECT/analysis/private/pon-auth.json}" --check-inputs-only
if [ ! -e "$TREE/package/feeds/packages/cgi-io" ] || [ ! -e "$TREE/package/feeds/luci/luci" ]; then
    sh "$PROJECT/scripts/prepare_luci_feeds.sh" "$TREE"
fi
sh "$PORT/scripts/sync-overlay.sh" "$TREE"
cd "$TREE"
REGDB_PATCHED=0
if ! grep -Fq 'regulatory.db.p7s' package/firmware/wireless-regdb/Makefile; then
    patch -p1 --fuzz=0 < "$PORT/host-patches/110-wireless-regdb-signed.patch"
    REGDB_PATCHED=1
fi
make defconfig
if [ "$REGDB_PATCHED" = 1 ]; then
    make package/firmware/wireless-regdb/clean
fi
# On this WSL host, TCP fakeroot fails at the first file operation. Use the
# existing upstream SysV backend, not root builds or altered package ownership.
if grep -qi microsoft /proc/sys/kernel/osrelease; then
    if grep -q -- '--with-ipc=tcp' tools/fakeroot/Makefile; then
        patch -p1 < "$PORT/host-patches/100-fakeroot-sysv.patch"
        make tools/fakeroot/clean
    fi
fi
make -j"$JOBS" download
set -- "$TREE"/build_dir/target-*/uci-*/uci
if [ "$#" != 1 ] || [ ! -x "$1" ]; then
    make -j"$JOBS" package/system/uci/compile
fi
export TYFZA1_UCI_TREE="$TREE"
python3 "$PROJECT/tools/prepare_pon_runtime.py" "$TREE/files" \
    --stock "${TYFZA1_STOCK_INPUTS:-$PROJECT/analysis/private/live-files}" \
    --auth-config "${TYFZA1_AUTH_CONFIG:-$PROJECT/analysis/private/pon-auth.json}"
python3 "$PROJECT/tools/prepare_wifi_runtime.py" "$TREE/files" --openwrt-tree "$TREE"
make -j"$JOBS" world
