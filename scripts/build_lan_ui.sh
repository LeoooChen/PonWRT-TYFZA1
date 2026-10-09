#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
P=/home/leo/PonWRT-TYFZA1
cd "$P"
KERNEL_VERSION=$(python3 port/tools/project_paths.py --field kernel)
# Full builds share one OpenWrt staging tree, so run them one at a time.
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
export http_proxy=http://host.orb.internal:7897 https_proxy=http://host.orb.internal:7897
export CURL_OPTIONS='--speed-time 60 --speed-limit 1024'
sh port/scripts/build.sh "$P/sources/openwrt" 2
cd "$P"
BIN=$P/sources/openwrt/bin/targets/rtl9617c/generic
OUT=${TYFZA1_LAN_OUT:-$P/artifacts/full-lan-luci-6.18.55}
mkdir -p "$OUT/firmware"
cp -a "$BIN/." "$OUT/firmware/"
set -- "$BIN"/*-initramfs-Image
.venv/bin/python scripts/make_ram_fit.py "$1" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-xram-lan.dtb" "$OUT/ram" --keys "$P/evidence/rtl-workspace/private/tyfza1-build-keys" --kernel-version "$KERNEL_VERSION" --cpus 4
.venv/bin/python scripts/make_ram_fit.py "$1" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-ni.dtb" "$OUT/ram-ni" --keys "$P/evidence/rtl-workspace/private/tyfza1-build-keys" --kernel-version "$KERNEL_VERSION" --cpus 4
.venv/bin/python scripts/make_ram_fit.py "$1" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-pon-status.dtb" "$OUT/ram-pon" --keys "$P/evidence/rtl-workspace/private/tyfza1-build-keys" --kernel-version "$KERNEL_VERSION" --cpus 4
python3 tools/verify_pcie_dtb.py "$P/evidence/wireless-study-20261008/evidence/stock/live-fit.dtb" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-pcie.dtb" --output "$OUT/pcie-dtb-verification.json"
.venv/bin/python scripts/make_ram_fit.py "$1" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-pcie.dtb" "$OUT/ram-pcie" --keys "$P/evidence/rtl-workspace/private/tyfza1-build-keys" --kernel-version "$KERNEL_VERSION" --cpus 4
python3 tools/verify_pcie_payload.py "$OUT"
KERNEL_DIRECTORY=$(python3 port/tools/project_paths.py --kernel-directory "$P/sources/openwrt")
python3 tools/verify_wifi_payload.py "$OUT" \
    --kernel-elf "$(dirname "$KERNEL_DIRECTORY")/vmlinux-initramfs.debug"
