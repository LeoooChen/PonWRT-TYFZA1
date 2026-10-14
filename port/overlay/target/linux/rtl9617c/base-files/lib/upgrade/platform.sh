#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
REQUIRE_IMAGE_METADATA=1
RAMFS_COPY_BIN='fw_printenv fw_setenv'
RAMFS_COPY_DATA='/etc/fw_env.config'
CI_UBIPART=ubi_device

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
	tyfza1_volume_fits "$ubidev" "ubi_r$slot" "$1" &&
		tyfza1_volume_fits "$ubidev" "ubi_k$slot" "$2" 6291456
}

platform_check_image() {
	nand_do_platform_check "$(board_name)" "$1" || return 1
	local board_dir=$(tar tf "$1" | grep -m 1 '^sysupgrade-.*/$')
	board_dir=${board_dir%/}
	local root_size=$(tar xOf "$1" "$board_dir/root" | wc -c)
	local kernel_size=$(tar xOf "$1" "$board_dir/kernel" | wc -c)
	tyfza1_check_sizes "$root_size" "$kernel_size"
}

# Keep the OEM kernel/root volume IDs required by the signed FIT boot script.
nand_upgrade_prepare_ubi() {
	tyfza1_check_sizes "$1" "$3" || return 1
	local ubidev=$(nand_find_ubi "$CI_UBIPART")
	local rootvol=$(nand_find_volume "$ubidev" "$CI_ROOTPART")
	local datavol=$(nand_find_volume "$ubidev" rootfs_data)
	nand_remove_ubiblock "$rootvol" || return 1
	[ -z "$datavol" ] || ubirmvol "/dev/$ubidev" -N rootfs_data || return 1
	ubimkvol "/dev/$ubidev" -n 11 -N rootfs_data -s 16777216
}

platform_do_upgrade() {
	local slot=$(sed -n 's/.*tyfza1.slot=\([01]\).*/\1/p' /proc/cmdline)
	case "$slot" in
		0|1) ;;
		*) echo "UBI sysupgrade requires a persistent TYFZA1 boot" >&2; return 1 ;;
	esac
	CI_KERNPART="ubi_k$slot"
	CI_ROOTPART="ubi_r$slot"
	nand_do_upgrade "$1"
}
