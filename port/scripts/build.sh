#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
export TYFZA1_CARGO=${TYFZA1_CARGO:-$(command -v cargo)}
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
if [ -n "${TYFZA1_PROXY:-}" ]; then
    export http_proxy=$TYFZA1_PROXY https_proxy=$TYFZA1_PROXY
fi
export CURL_OPTIONS='--speed-time 60 --speed-limit 1024'
umask 022
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PROJECT=$(CDPATH= cd -- "$PORT/.." && pwd)
export TYFZA1_PROJECT=$PROJECT
TREE=${1:?Usage: build.sh /path/to/openwrt-checkout [jobs]}
TREE=$(realpath "$TREE")
JOBS=${2:-2}
if [ -z "${TYFZA1_RUNTIME_INPUTS:-}" ]; then
    python3 "$PROJECT/tools/prepare_pon_runtime.py" "$TREE/files" \
        --stock "${TYFZA1_STOCK_INPUTS:-$PROJECT/analysis/private/live-files}" \
        --check-inputs-only
fi
PE_RADIO_CFLAGS=-DPE_UPLINK sh "$PROJECT/scripts/build_pe_radio_wd.sh" "$PROJECT/artifacts/pe-radio-production"
cp "$PROJECT/artifacts/pe-radio-production/pe-tx-code.h" \
    "$PORT/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/pe-tx-code.h"
sh "$PORT/scripts/sync-overlay.sh" "$TREE"
sh "$PROJECT/scripts/prepare_luci_feeds.sh" "$TREE"
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
if [ -n "${TYFZA1_RUNTIME_INPUTS:-}" ]; then
    python3 "$PROJECT/tools/install_runtime_inputs.py" "$TYFZA1_RUNTIME_INPUTS" "$TREE/files"
else
    python3 "$PROJECT/tools/prepare_pon_runtime.py" "$TREE/files" \
        --stock "${TYFZA1_STOCK_INPUTS:-$PROJECT/analysis/private/live-files}"
    python3 "$PROJECT/tools/prepare_wifi_runtime.py" "$TREE/files" --openwrt-tree "$TREE"
fi
make -j"$JOBS" world
