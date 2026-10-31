#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
cd "$(dirname "$0")"
SDK=${1:?Usage: build.sh OPENWRT_BUILD_TREE OUTPUT_DIRECTORY}
OUT=${2:?Output directory required}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
set -- "$SDK"/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl
if [ "$#" != 1 ] || [ ! -d "$1" ]; then
	echo 'Exactly one Cortex-A55 toolchain required' >&2
	exit 1
fi
TOOLCHAIN=$1
export STAGING_DIR="$SDK/staging_dir"
"$TOOLCHAIN/bin/aarch64-openwrt-linux-musl-gcc" \
	-std=c11 -Os -static -mcpu=cortex-a55 -Wall -Wextra -Werror \
	-ffunction-sections -fdata-sections -Wl,--gc-sections \
	recorder.c -o "$OUT/tyfza1-recorderd"
"$TOOLCHAIN/bin/aarch64-openwrt-linux-musl-strip" "$OUT/tyfza1-recorderd"
cp tyfza1-recorder tyfza1-recorder.init install.sh recorder.c test_recorder.c build.sh README.md "$OUT/"
chmod 755 "$OUT/tyfza1-recorderd" "$OUT/tyfza1-recorder" "$OUT/install.sh"
cd "$OUT"
sha256sum tyfza1-recorderd tyfza1-recorder tyfza1-recorder.init install.sh \
	recorder.c test_recorder.c build.sh README.md > SHA256SUMS
file tyfza1-recorderd
sha256sum tyfza1-recorderd
