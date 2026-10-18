#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only

tyfza1_find_volume() {
	local path
	for path in /sys/class/ubi/"$1"_*/name; do
		[ -f "$path" ] || continue
		[ "$(cat "$path")" = "$2" ] || continue
		path=${path%/name}
		echo "${path##*/}"
		return 0
	done
	return 1
}

# Only the selected OpenWrt root and rootfs_data participate in allocation.
# Keep every OEM volume, including the other boot slot, at its original size.
tyfza1_plan_storage() {
	local ubidev="$1" rootvol="$2" size="$3"
	local path="/sys/class/ubi/$rootvol" data free root_ebs data_ebs=0
	local leb=$(cat "$path/usable_eb_size")
	case "$(cat "$path/name"):$rootvol" in
		ubi_r0:${ubidev}_4|ubi_r1:${ubidev}_7) ;;
		*) echo "Unexpected TYFZA1 root volume: $rootvol" >&2; return 1 ;;
	esac
	[ "$size" -gt 0 ] && [ "$size" -le 33648640 ] || {
		echo "Root image must fit the 33648640-byte OEM slot limit" >&2
		return 1
	}
	free=$(cat "/sys/class/ubi/$ubidev/avail_eraseblocks")
	root_ebs=$(cat "$path/reserved_ebs")
	data=$(tyfza1_find_volume "$ubidev" rootfs_data) || data=
	if [ -n "$data" ]; then
		[ "$data" = "${ubidev}_11" ] || return 1
		data_ebs=$(cat "/sys/class/ubi/$data/reserved_ebs")
	else
		# Never overwrite a differently named volume occupying ID 11.
		[ ! -e "/sys/class/ubi/${ubidev}_11" ] || return 1
	fi
	TYFZA1_ROOT_BYTES=$(( (size + leb - 1) / leb * leb ))
	TYFZA1_DATA_BYTES=$(( (free + root_ebs + data_ebs) * leb - TYFZA1_ROOT_BYTES ))
	[ "$TYFZA1_DATA_BYTES" -ge 16777216 ] || {
		echo "Insufficient UBI space for rootfs and writable data" >&2
		return 1
	}
}

tyfza1_resize_root() {
	local ubidev="$1" rootvol="$2"
	ubirsvol "/dev/$ubidev" -N "$(cat "/sys/class/ubi/$rootvol/name")" \
		-s "$TYFZA1_ROOT_BYTES"
}

tyfza1_grow_data() {
	local ubidev="$1" data current
	data=$(tyfza1_find_volume "$ubidev" rootfs_data) || data=
	if [ -n "$data" ]; then
		current=$(( $(cat "/sys/class/ubi/$data/reserved_ebs") * $(cat "/sys/class/ubi/$data/usable_eb_size") ))
		[ "$current" -le "$TYFZA1_DATA_BYTES" ] || {
			echo "Existing writable data cannot be shrunk; upgrade from OpenWrt" >&2
			return 1
		}
		[ "$current" -eq "$TYFZA1_DATA_BYTES" ] || \
			ubirsvol "/dev/$ubidev" -N rootfs_data -s "$TYFZA1_DATA_BYTES"
	else
		ubimkvol "/dev/$ubidev" -n 11 -N rootfs_data -m
	fi
}

# Older sysupgrade implementations create a 16 MiB UBIFS before rebooting.
# Read the installed SquashFS size without changing the mounted root volume.
tyfza1_boot_storage_plan() {
	local slot rootvol data current max_lebs
	slot=$(sed -n 's/.*tyfza1.slot=\([01]\).*/\1/p' /proc/cmdline)
	case "$slot" in 0|1) ;; *) return 1 ;; esac
	rootvol=$(tyfza1_find_volume ubi0 "ubi_r$slot") || return 1
	[ "$(hexdump -v -n 4 -e '1/1 "%02x"' "/dev/$rootvol")" = 68737173 ] || return 1
	set -- $(hexdump -v -s 40 -n 8 -e '2/4 "%u "' "/dev/$rootvol")
	[ "$#" -eq 2 ] && [ "$2" -eq 0 ] || return 1
	tyfza1_plan_storage ubi0 "$rootvol" "$(( ($1 + 1023) / 1024 * 1024 ))" || return 1
	TYFZA1_BOOT_ROOTVOL=$rootvol
	current=$(( $(cat "/sys/class/ubi/$rootvol/reserved_ebs") * $(cat "/sys/class/ubi/$rootvol/usable_eb_size") ))
	[ "$current" -ne "$TYFZA1_ROOT_BYTES" ] && return 0
	[ "$(cat /sys/class/ubi/ubi0/avail_eraseblocks)" -gt 0 ] && return 0
	data=$(tyfza1_find_volume ubi0 rootfs_data) || return 0
	# A kernel-created UBIFS cannot grow beyond its original max_leb_cnt.
	[ "$(hexdump -v -n 4 -e '1/1 "%02x"' "/dev/$data")" = 31181006 ] || return 1
	max_lebs=$(hexdump -v -s 44 -n 4 -e '1/4 "%u"' "/dev/$data")
	[ "$max_lebs" -lt "$((TYFZA1_DATA_BYTES / $(cat "/sys/class/ubi/$data/usable_eb_size")))" ]
}

# Called by stage2 after PID 1 and the shell have moved to the upgrade ramfs.
tyfza1_migrate_storage() {
	tyfza1_boot_storage_plan || return 1
	local data=$(tyfza1_find_volume ubi0 rootfs_data)
	local saved=/tmp/tyfza1-overlay.tar mountpoint=/tmp/tyfza1-overlay
	mkdir -p "$mountpoint" || return 1
	if [ -n "$data" ]; then
		mount -t ubifs -o ro "/dev/$data" "$mountpoint" || return 1
		tar -cf "$saved" -C "$mountpoint" . || {
			umount "$mountpoint"
			return 1
		}
		umount "$mountpoint" || return 1
	fi
	nand_remove_ubiblock "$TYFZA1_BOOT_ROOTVOL" || return 1
	# Resize in place so the already installed SquashFS remains intact.
	tyfza1_resize_root ubi0 "$TYFZA1_BOOT_ROOTVOL" || return 1
	[ -z "$data" ] || ubirmvol /dev/ubi0 -N rootfs_data || return 1
	tyfza1_grow_data ubi0 || return 1
	if [ -f "$saved" ]; then
		data=$(tyfza1_find_volume ubi0 rootfs_data)
		mount -t ubifs "/dev/$data" "$mountpoint" || return 1
		tar -xf "$saved" -C "$mountpoint" || return 1
		umount "$mountpoint" || return 1
	fi
	sync
}
