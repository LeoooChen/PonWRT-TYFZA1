#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Run from the extracted installation bundle in the device's telnet shell.
set -eu
cd "${0%/*}"

volume() {
	for path in /sys/class/ubi/ubi0_*/name; do
		if [ "$(cat "$path")" = "$1" ]; then
			path=${path%/name}
			echo "/dev/${path##*/}"
			return
		fi
	done
	return 1
}

image_fits_volume() {
	local path="/sys/class/ubi/${1##*/}"
	local size=$(wc -c < "$2")
	local capacity=$(( $(cat "$path/reserved_ebs") * $(cat "$path/usable_eb_size") ))
	local limit=${3:-$capacity}
	[ "$capacity" -le "$limit" ] || capacity=$limit
	[ "$size" -le "$capacity" ] || {
		echo "$2 is $size bytes; $1 image limit is $capacity bytes" >&2
		return 1
	}
}

if [ "${1:-install}" = commit ]; then
	slot=$(sed -n 's/.*tyfza1.slot=\([01]\).*/\1/p' /proc/cmdline)
	case "$slot" in
		0|1) ;;
		*) echo "Commit requires the TYFZA1 UBI boot" >&2; exit 1 ;;
	esac
	printf 'sw_commit %s\nsw_tryactive 2\n' "$slot" > /tmp/tyfza1-boot.env
	./fw_setenv -c ./fw_env.config -s /tmp/tyfza1-boot.env
	./fw_printenv -c ./fw_env.config sw_commit sw_tryactive
	exit
fi

active=$(./fw_printenv -c ./fw_env.config -n sw_active)
slot=$((1 - active))
kernel=$(volume "ubi_k$slot")
rootfs=$(volume "ubi_r$slot")
image_fits_volume "$rootfs" rootfs.squashfs
image_fits_volume "$kernel" kernel.itb 6291456
echo "Updating existing ubi_k$slot and ubi_r$slot"
ubiupdatevol "$rootfs" rootfs.squashfs
ubiupdatevol "$kernel" kernel.itb
if ! volume rootfs_data >/dev/null; then
	ubimkvol /dev/ubi0 -n 11 -N rootfs_data -s 16777216
fi
if [ "${1:-install}" = trial ]; then
	printf 'sw_tryactive %s\n' "$slot" > /tmp/tyfza1-boot.env
else
	printf 'sw_commit %s\nsw_tryactive 2\n' "$slot" > /tmp/tyfza1-boot.env
fi
./fw_setenv -c ./fw_env.config -s /tmp/tyfza1-boot.env
sync
echo "Images installed; reboot starts the selected OpenWrt image."
reboot
