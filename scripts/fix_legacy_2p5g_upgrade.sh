#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Fix only the legacy 2.5G board's shared-archive name check; never flash here.
set -eu
[ "$(cat /tmp/sysinfo/board_name)" = 'tianyi,tyfza1-2p5g' ] || {
    echo 'This fix applies only to tianyi,tyfza1-2p5g.' >&2
    exit 1
}
image=${1:?Usage: sh fix_legacy_2p5g_upgrade.sh /tmp/sysupgrade.bin}
[ -f "$image" ] || { echo 'Image file not found.' >&2; exit 1; }
platform=/lib/upgrade/platform.sh
old='nand_do_platform_check "$(board_name)" "$1" || return 1'
new='nand_do_platform_check tianyi,tyfza1 "$1" || return 1'
if grep -Fq "$new" "$platform"; then
    exec sysupgrade -T "$image"
fi
grep -Fq "$old" "$platform" || {
    echo 'Unrecognized upgrade script; no changes made.' >&2
    exit 1
}
backup=$(mktemp /tmp/tyfza1-platform-before-fix.XXXXXX)
cp "$platform" "$backup"
sed 's/nand_do_platform_check "$(board_name)"/nand_do_platform_check tianyi,tyfza1/' "$backup" > "$platform"
if sysupgrade -T "$image"; then
    echo "Image checks passed. Script backup: $backup"
    echo 'No Flash partitions were written. Review the result before upgrading.'
else
    cp "$backup" "$platform"
    echo "Image checks failed; original upgrade script restored from $backup." >&2
    exit 1
fi
