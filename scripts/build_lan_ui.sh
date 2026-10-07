#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
P=/home/leo/PonWRT-TYFZA1
cd "$P"
export http_proxy=http://host.orb.internal:7897 https_proxy=http://host.orb.internal:7897
export CURL_OPTIONS='--speed-time 60 --speed-limit 1024'
if [ ! -e "$P/sources/openwrt/package/feeds/packages/cgi-io" ] || [ ! -e "$P/sources/openwrt/package/feeds/luci/luci" ]; then
    sh scripts/prepare_luci_feeds.sh "$P/sources/openwrt"
fi
sh port/scripts/sync-overlay.sh "$P/sources/openwrt"
python3 tools/prepare_pon_serdes.py "$P/analysis/private/live-files/etc/serdes" "$P/artifacts/pon-serdes-firmware"
mkdir -p "$P/sources/openwrt/files/lib/firmware/realtek/rtl9617c"
cp "$P/artifacts/pon-serdes-firmware/"*.bin "$P/sources/openwrt/files/lib/firmware/realtek/rtl9617c/"
cd sources/openwrt
make defconfig
make -j2 download
make -j2 world
cd "$P"
BIN=$P/sources/openwrt/bin/targets/rtl9617c/generic
OUT=${TYFZA1_LAN_OUT:-$P/artifacts/full-lan-luci-6.18.55}
mkdir -p "$OUT/firmware"
cp -a "$BIN/." "$OUT/firmware/"
set -- "$BIN"/*-initramfs-Image
.venv/bin/python scripts/make_ram_fit.py "$1" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-xram-lan.dtb" "$OUT/ram" --keys "$P/evidence/rtl-workspace/private/tyfza1-build-keys" --kernel-version 6.18.55 --cpus 4
.venv/bin/python scripts/make_ram_fit.py "$1" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-ni.dtb" "$OUT/ram-ni" --keys "$P/evidence/rtl-workspace/private/tyfza1-build-keys" --kernel-version 6.18.55 --cpus 4
.venv/bin/python scripts/make_ram_fit.py "$1" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-pon-status.dtb" "$OUT/ram-pon" --keys "$P/evidence/rtl-workspace/private/tyfza1-build-keys" --kernel-version 6.18.55 --cpus 4
