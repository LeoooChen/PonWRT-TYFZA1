#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TREE=${1:-$P/sources/openwrt}
TREE=$(realpath "$TREE")
PACKAGES=$(sed -n 's/^CONFIG_PACKAGE_\([^=]*\)=[ym]$/\1/p' "$TREE/.config")
cp "$P/configs/feeds.conf" "$TREE/feeds.conf"
cd "$TREE"
if [ -n "${TYFZA1_PROXY:-}" ]; then
    export http_proxy=$TYFZA1_PROXY https_proxy=$TYFZA1_PROXY
fi
# Argon is a single-package repository, not a feed containing package folders.
# Its directory must also be named luci-theme-argon for luci.mk to name the package.
ARGON="$TREE/feeds/luci-theme-argon"
set -- $(python3 -c 'import json,sys; s=json.load(open(sys.argv[1]))["luci_theme_argon"]; print(s["url"],s["commit"])' "$P/port/sources.lock.json")
mkdir -p "$TREE/feeds" "$TREE/package"
if [ ! -d "$ARGON/.git" ]; then
    git init "$ARGON"
fi
if ! git -C "$ARGON" diff --quiet || ! git -C "$ARGON" diff --cached --quiet; then
    echo "Argon checkout has local changes; refusing to replace them" >&2
    exit 1
fi
if [ "$(git -C "$ARGON" rev-parse --verify HEAD 2>/dev/null || true)" != "$2" ]; then
    git -C "$ARGON" fetch --depth=1 "$1" "$2"
    git -C "$ARGON" checkout --detach "$2"
fi
if [ -e "$TREE/package/luci-theme-argon" ] && [ ! -L "$TREE/package/luci-theme-argon" ]; then
    echo "package/luci-theme-argon already exists and is not a managed symlink" >&2
    exit 1
fi
ln -sfn "$ARGON" "$TREE/package/luci-theme-argon"
./scripts/feeds update packages luci
sh "$P/port/scripts/apply-luci-patches.sh" "$TREE"
printf '%s\n' "$PACKAGES" | xargs ./scripts/feeds install cgi-io luasrcdiet luci
# Existing build trees retain .config; select the new default there as well.
sed -i '/^CONFIG_PACKAGE_luci-theme-argon=/d; /^# CONFIG_PACKAGE_luci-theme-argon is not set$/d; /^CONFIG_PACKAGE_luci-i18n-argon-config-zh-cn=/d; /^# CONFIG_PACKAGE_luci-i18n-argon-config-zh-cn is not set$/d' .config
printf '%s\n' 'CONFIG_PACKAGE_luci-theme-argon=y' 'CONFIG_PACKAGE_luci-i18n-argon-config-zh-cn=y' >> .config
# Each LuCI app owns its translation catalog; base and theme catalogs do not cover it.
sed -i '/^CONFIG_LUCI_LANG_zh_Hans=/d; /^# CONFIG_LUCI_LANG_zh_Hans is not set$/d' .config
printf '%s\n' 'CONFIG_LUCI_LANG_zh_Hans=y' >> .config
