#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build only; run the isolated test module explicitly on a matching kernel.
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:-$P/artifacts/wifi-miss-gro-test}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
DRIVERS=$P/port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek
cp "$P/tools/test_wifi_miss_gro.c" "$OUT/"
python3 - "$DRIVERS/rtl9617c-wifi.c" "$OUT/wifi-receive-under-test.h" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
start = source.index('static void wifi_receive(')
end = source.index('\nstatic struct rtl_wifi_port *wifi_port', start)
Path(sys.argv[2]).write_text(source[start:end] + '\n')
PY
printf 'obj-m := test_wifi_miss_gro.o\nccflags-y += -I%s\n' "$DRIVERS" > "$OUT/Makefile"
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH" STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- modules
