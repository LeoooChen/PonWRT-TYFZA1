#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
REQUIRE_IMAGE_METADATA=1
RAMFS_COPY_BIN='fw_printenv fw_setenv fwtool jsonfilter'
RAMFS_COPY_DATA='/etc/fw_env.config'
CI_UBIPART=ubi_device
. /lib/upgrade/tyfza1-storage.sh

tyfza1_volume_fits() {
	local volume=$(nand_find_volume "$1" "$2")
	local path="/sys/class/ubi/$volume"
	local capacity=$(( $(cat "$path/reserved_ebs") * $(cat "$path/usable_eb_size") ))
	local limit=${4:-$capacity}
	[ "$capacity" -le "$limit" ] || capacity=$limit
	[ "$3" -le "$capacity" ] || {
		echo "$2 image is $3 bytes; volume image limit is $capacity bytes" >&2
		return 1
	}
}

tyfza1_check_sizes() {
	local slot=$(sed -n 's/.*tyfza1.slot=\([01]\).*/\1/p' /proc/cmdline)
	local ubidev=$(nand_find_ubi "$CI_UBIPART")
	case "$slot" in
		0|1) ;;
		*) echo "UBI sysupgrade requires a persistent TYFZA1 boot" >&2; return 1 ;;
	esac
	local rootvol=$(nand_find_volume "$ubidev" "ubi_r$slot")
	tyfza1_plan_storage "$ubidev" "$rootvol" "$1" &&
		tyfza1_volume_fits "$ubidev" "ubi_k$slot" "$2" 6291456
}

# A migrated unit has no ubi_Config. Older images cannot import its calibration.
tyfza1_check_storage_format() {
	local entry name migrated=0 metadata=/tmp/tyfza1-storage-image.json
	for entry in /sys/class/ubi/ubi0_12/name /sys/class/ubi/ubi0_13/name; do
		[ -f "$entry" ] || continue
		read -r name < "$entry"
		case "$name" in tyfza1_factory|tyfza1_factory_backup) migrated=1;; esac
	done
	[ "$migrated" = 1 ] || return 0
	fwtool -q -i "$metadata" "$1" || return 1
	[ "$(jsonfilter -i "$metadata" -e '@.tyfza1_storage')" = factory-v1 ] || {
		echo 'This unit requires factory-v1 firmware; restore the OEM layout before downgrading' >&2
		return 1
	}
}

platform_check_image() {
	case "$(board_name)" in
		tianyi,tyfza1|tianyi,tyfza1-2p5g) ;;
		*) echo "Unsupported TYFZA1 board" >&2; return 1 ;;
	esac
	tyfza1_check_storage_format "$1" || return 1
	# Both FIT configurations share one sysupgrade archive directory.
	nand_do_platform_check tianyi,tyfza1 "$1" || return 1
	local board_dir=$(tar tf "$1" | grep -m 1 '^sysupgrade-.*/$')
	board_dir=${board_dir%/}
	local root_size=$(tar xOf "$1" "$board_dir/root" | wc -c)
	local kernel_size=$(tar xOf "$1" "$board_dir/kernel" | wc -c)
	tyfza1_check_sizes "$root_size" "$kernel_size"
}

# Keep the OEM kernel/root volume IDs required by the signed FIT boot script.
nand_upgrade_prepare_ubi() {
	[ "$2" = squashfs ] || return 1
	tyfza1_check_sizes "$1" "$3" || return 1
	local ubidev=$(nand_find_ubi "$CI_UBIPART")
	local rootvol=$(nand_find_volume "$ubidev" "$CI_ROOTPART")
	local datavol=$(nand_find_volume "$ubidev" rootfs_data)
	nand_remove_ubiblock "$rootvol" || return 1
	[ -z "$datavol" ] || ubirmvol "/dev/$ubidev" -N rootfs_data || return 1
	# Clear the old image before shrinking, including on static UBI volumes.
	ubiupdatevol "/dev/$rootvol" -t || return 1
	tyfza1_resize_root "$ubidev" "$rootvol" || return 1
	tyfza1_grow_data "$ubidev"
}

platform_do_upgrade() {
	# Recheck here as sysupgrade -F can bypass platform_check_image.
	tyfza1_check_storage_format "$1" || return 1
	local slot=$(sed -n 's/.*tyfza1.slot=\([01]\).*/\1/p' /proc/cmdline)
	case "$slot" in
		0|1) ;;
		*) echo "UBI sysupgrade requires a persistent TYFZA1 boot" >&2; return 1 ;;
	esac
	CI_KERNPART="ubi_k$slot"
	CI_ROOTPART="ubi_r$slot"
	nand_do_upgrade "$1"
}
