#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build only; run explicitly on a matching kernel.
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:-$P/artifacts/wifi-tx-sg-test}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
DRIVER=$P/port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap
cp "$P/tools/test_wifi_tx_sg.c" "$OUT/"
python3 - "$DRIVER/monitor.h" "$OUT/wifi-tx-features-under-test.h" <<'PY'
from pathlib import Path
import re
import sys
source = Path(sys.argv[1]).read_text()
features = re.findall(r'hw->netdev_features\s*=\s*([^;]+);', source)
assert len(features) <= 1
Path(sys.argv[2]).write_text('#define WIFI_TX_FEATURES (' + (features[0] if features else '0') + ')\n')
PY
printf 'obj-m := test_wifi_tx_sg.o\n' > "$OUT/Makefile"
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH" STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- modules
