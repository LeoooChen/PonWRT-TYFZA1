#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Run from the extracted sysupgrade bundle, including on older firmware.
set -eu
cd "${0%/*}"
case "$(sed -n 's/.*tyfza1.slot=\([01]\).*/\1/p' /proc/cmdline)" in
	0|1) ;;
	*) echo "Upgrade requires the TYFZA1 UBI boot" >&2; exit 1 ;;
esac
cp platform.sh tyfza1-storage.sh /lib/upgrade/
exec sysupgrade "$@" "$PWD/sysupgrade.bin"
