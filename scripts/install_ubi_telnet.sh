#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Run from the extracted installation bundle in the device's telnet shell.
set -eu
cd "${0%/*}"
export PATH="$PWD:$PATH"
. ./tyfza1-storage.sh

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

for tool in ubirsvol ubiupdatevol ubimkvol; do
	command -v "$tool" >/dev/null || { echo "Missing storage tool: $tool" >&2; exit 1; }
done
active=$(./fw_printenv -c ./fw_env.config -n sw_active)
case "$active" in
	0|1) ;;
	*) echo "Invalid active OEM slot: $active" >&2; exit 1 ;;
esac
slot=$((1 - active))
kernel=$(volume "ubi_k$slot")
rootfs=$(volume "ubi_r$slot")
image_fits_volume "$kernel" kernel.itb 6291456
tyfza1_plan_storage ubi0 "${rootfs##*/}" "$(wc -c < rootfs.squashfs)"
# A previous installation may already have a smaller root volume. Growing it
# here must not consume blocks belonging to an existing UBIFS data volume.
current=$(( $(cat "/sys/class/ubi/${rootfs##*/}/reserved_ebs") * $(cat "/sys/class/ubi/${rootfs##*/}/usable_eb_size") ))
if [ "$TYFZA1_ROOT_BYTES" -gt "$current" ]; then
	free=$(( $(cat /sys/class/ubi/ubi0/avail_eraseblocks) * $(cat "/sys/class/ubi/${rootfs##*/}/usable_eb_size") ))
	[ "$((TYFZA1_ROOT_BYTES - current))" -le "$free" ] || {
		echo "Upgrade from OpenWrt to resize an existing installation" >&2
		exit 1
	}
	tyfza1_resize_root ubi0 "${rootfs##*/}"
fi
echo "Updating existing ubi_k$slot and ubi_r$slot"
ubiupdatevol "$rootfs" rootfs.squashfs
ubiupdatevol "$kernel" kernel.itb
tyfza1_resize_root ubi0 "${rootfs##*/}"
tyfza1_grow_data ubi0
if [ "${1:-install}" = trial ]; then
	printf 'sw_tryactive %s\n' "$slot" > /tmp/tyfza1-boot.env
else
	printf 'sw_commit %s\nsw_tryactive 2\n' "$slot" > /tmp/tyfza1-boot.env
fi
./fw_setenv -c ./fw_env.config -s /tmp/tyfza1-boot.env
sync
echo "Images installed; reboot starts the selected OpenWrt image."
reboot
