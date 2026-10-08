#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build the current OpenWrt-patched kernel with the already booted native rootfs.
set -eu
P=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
K=$P/artifacts/kernel-source/linux-6.18.55
B=$P/artifacts/kernel-build-6.18.55
R=$P/artifacts/bringup-rootfs
G=$P/sources/openwrt/target/linux/generic
if [ ! -f "$K/.tyfza1-prepared" ]; then
    for kind in backport pending hack; do
        sh "$P/sources/openwrt/scripts/patch-kernel.sh" "$K" "$G/$kind-6.18"
    done
    cp -a "$G/files/." "$K/"
    sh "$P/sources/openwrt/scripts/patch-kernel.sh" "$K" "$P/port/overlay/target/linux/rtl9617c/patches-6.18"
    touch "$K/.tyfza1-prepared"
fi
cp -a "$P/port/overlay/target/linux/rtl9617c/files-6.18/." "$K/"
mkdir -p "$B" "$R"
if [ ! -e "$R/sbin/init" ]; then
    tar -xzf "$P/artifacts/prior-native-20261002/firmware/openwrt-rtl9617c-generic-tianyi_tyfza1-rootfs.tar.gz" -C "$R"
fi
python3 "$P/tools/prepare_pon_serdes.py" "$P/analysis/private/live-files/etc/serdes" "$P/artifacts/pon-serdes-firmware"
mkdir -p "$R/lib/firmware/realtek/rtl9617c"
cp "$P/artifacts/pon-serdes-firmware/"*.bin "$R/lib/firmware/realtek/rtl9617c/"
cp "$P/analysis/private/live-files/var/config/rtkbosa_k.bin" "$R/lib/firmware/realtek/rtl9617c/bosa-calibration.bin"
python3 "$P/tools/prepare_pon_identity.py" "$P/analysis/private/live-files/var/config/config.xml" \
    "$P/analysis/private/live-files/var/config/default_scfg.txt" "$P/artifacts/pon-config/pon" \
    --auth-config "$P/analysis/private/pon-auth.json"
install -m 600 "$P/artifacts/pon-config/pon" "$R/etc/config/pon"
printf 'file /init %s 0755 0 0\n' "$G/other-files/init" > "$B/init-cpio-list"
cp "$P/artifacts/prior-native-20261002/verification/kernel.config" "$B/.config"
"$K/scripts/config" --file "$B/.config" --set-str INITRAMFS_SOURCE "$R $B/init-cpio-list $G/image/initramfs-base-files.txt" --set-val INITRAMFS_ROOT_UID 501 --set-val INITRAMFS_ROOT_GID 501
"$K/scripts/config" --file "$B/.config" --enable NET_VENDOR_REALTEK --enable RTL9617C_XRAM_ETH --enable REALTEK_PHY --enable RTL9617C_PON
"$K/scripts/config" --file "$B/.config" --enable RTL9617C_BOSA
"$K/scripts/config" --file "$B/.config" --enable I2C --enable I2C_CHARDEV --enable I2C_RTL9617C
"$K/scripts/config" --file "$B/.config" --enable PINCTRL --enable PINCTRL_RTL9617C
make -C "$K" O="$B" ARCH=arm64 olddefconfig
make -C "$K" O="$B" ARCH=arm64 -j2 Image
sh "$P/port/scripts/check-dtb.sh" "$K" "$P/artifacts/dtb-6.18.55" "$P/analysis/private/live-fit.dtb"
sha256sum "$B/arch/arm64/boot/Image"
