#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Cross-build and run native TYFZA1 OAM in an isolated network.
set -eu
PORT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PROJECT=$(CDPATH= cd -- "$PORT/.." && pwd)
TREE=$(realpath "${1:-$PROJECT/sources/openwrt}")
OUT=${2:?Usage: check-pon-userspace.sh openwrt-checkout new-output-dir}
test ! -e "$OUT"
test "$(git -C "$TREE" rev-parse HEAD)" = "$(python3 "$PORT/tools/project_paths.py" --field openwrt-commit)"
set -- "$TREE"/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin/aarch64-openwrt-linux-musl-gcc
test "$#" = 1 && test -x "$1"
CC=$1
export STAGING_DIR="$TREE/staging_dir/target-aarch64_cortex-a55_musl"
mkdir -p "$OUT"
OUT=$(realpath "$OUT")
SOURCE=$PORT/packages/tyfza1-pon-control/src
"$CC" -Wall -Wextra -Werror -O2 -I"$STAGING_DIR/usr/include" "$SOURCE/pon-oamd.c" "$SOURCE/pon-oam.c" -L"$STAGING_DIR/usr/lib" -luci -o "$OUT/pon-oamd"
sudo -n unshare --net python3 "$PROJECT/scripts/test_oam_daemon.py" --binary "$OUT/pon-oamd"
sha256sum "$OUT/pon-oamd" > "$OUT/SHA256SUMS"
