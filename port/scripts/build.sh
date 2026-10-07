#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export http_proxy=${TYFZA1_PROXY:-http://host.orb.internal:7897}
export https_proxy=$http_proxy
export CURL_OPTIONS='--speed-time 30 --speed-limit 1024 --max-time 300'
umask 022
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TREE=${1:?Usage: build.sh /path/to/openwrt-checkout [jobs]}
JOBS=${2:-8}
sh "$PORT/scripts/sync-overlay.sh" "$TREE"
cd "$TREE"
make defconfig
# On this WSL host, TCP fakeroot fails at the first file operation. Use the
# existing upstream SysV backend, not root builds or altered package ownership.
if grep -qi microsoft /proc/sys/kernel/osrelease; then
    if grep -q -- '--with-ipc=tcp' tools/fakeroot/Makefile; then
        patch -p1 < "$PORT/host-patches/100-fakeroot-sysv.patch"
        make tools/fakeroot/clean
    fi
fi
make -j"$JOBS" download
make -j"$JOBS" world
