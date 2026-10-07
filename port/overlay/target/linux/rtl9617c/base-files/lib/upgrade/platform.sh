#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
REQUIRE_IMAGE_METADATA=1

platform_check_image() {
	echo "TYFZA1 persistent upgrade awaits live NAND/ECC and bootloader verification." >&2
	return 1
}

platform_do_upgrade() {
	echo "No verified TYFZA1 UBI installation contract; no flash operation performed." >&2
	return 1
}
