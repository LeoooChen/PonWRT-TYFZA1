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
sh "$PORT/scripts/apply-openwrt-patches.sh" "$TREE"
mkdir -p "$TREE/target/linux/rtl9617c"
rsync -a --delete "$PORT/overlay/target/linux/rtl9617c/" "$TREE/target/linux/rtl9617c/"
mkdir -p "$TREE/package/luci-app-tyfza1-pon"
rsync -a --delete "$PORT/packages/luci-app-tyfza1-pon/" "$TREE/package/luci-app-tyfza1-pon/"
mkdir -p "$TREE/package/tyfza1-pon-control"
rsync -a --delete "$PORT/packages/tyfza1-pon-control/" "$TREE/package/tyfza1-pon-control/"
mkdir -p "$TREE/package/tyfza1-storage"
rsync -a --delete "$PORT/packages/tyfza1-storage/" "$TREE/package/tyfza1-storage/"
# hostapd patches apply to its unpacked source, not the OpenWrt tree root.
cp "$PORT/host-patches/471-hostapd-acs-no-survey.patch" \
    "$TREE/package/network/services/hostapd/patches/471-hostapd-acs-no-survey.patch"
mkdir -p "$TREE/package/network/config/firewall4/patches"
cp "$PORT/host-patches/120-firewall4-netifd-lower-devices.patch" \
    "$TREE/package/network/config/firewall4/patches/120-firewall4-netifd-lower-devices.patch"
cp "$PORT/host-patches/121-firewall4-interface-topology.patch" \
    "$TREE/package/network/config/firewall4/patches/121-firewall4-interface-topology.patch"
mkdir -p "$TREE/package/network/config/netifd/patches"
cp "$PORT/host-patches/120-netifd-interface-topology.patch" \
    "$TREE/package/network/config/netifd/patches/120-netifd-interface-topology.patch"
mkdir -p "$TREE/package/network/utils/iwinfo/patches"
cp "$PORT/host-patches/130-iwinfo-txpower-unknown.patch" \
    "$TREE/package/network/utils/iwinfo/patches/130-iwinfo-txpower-unknown.patch"
if [ -d "$TREE/feeds/luci/modules/luci-mod-status" ]; then
    sh "$PORT/scripts/apply-luci-patches.sh" "$TREE"
fi
if [ -n "${TYFZA1_CONFIG:-}" ]; then
    cp "$TYFZA1_CONFIG" "$TREE/.config"
elif [ ! -s "$TREE/.config" ]; then
    cp "$PORT/configs/tyfza1-boot.config" "$TREE/.config"
fi
VERSION=$(cat "$PORT/../VERSION")
# Keep package choices, but always identify firmware by the project version.
sed -i '/^CONFIG_VERSIONOPT=/d; /^# CONFIG_VERSIONOPT is not set$/d; /^CONFIG_VERSION_NUMBER=/d; /^CONFIG_VERSION_REPO=/d; /^CONFIG_SIGNED_PACKAGES=/d; /^# CONFIG_SIGNED_PACKAGES is not set$/d; /^CONFIG_SIGN_EACH_PACKAGE=/d; /^# CONFIG_SIGN_EACH_PACKAGE is not set$/d' "$TREE/.config"
printf 'CONFIG_VERSIONOPT=y\nCONFIG_VERSION_NUMBER="%s"\n' "$VERSION" >> "$TREE/.config"
printf 'CONFIG_VERSION_REPO="https://downloads.openwrt.org/snapshots"\nCONFIG_SIGNED_PACKAGES=y\nCONFIG_SIGN_EACH_PACKAGE=y\n' >> "$TREE/.config"
# Keep the common plugin dependencies in existing and custom build configs.
while IFS= read -r setting; do
    case "$setting" in
        CONFIG_PACKAGE_*=y)
            option=${setting%%=*}
            sed -i "/^$option=/d; /^# $option is not set$/d" "$TREE/.config"
            printf '%s\n' "$setting" >> "$TREE/.config"
            ;;
    esac
done < "$PORT/configs/tyfza1-compat.config"
echo "Applied RTL9617C overlay to $TREE at $PIN"
