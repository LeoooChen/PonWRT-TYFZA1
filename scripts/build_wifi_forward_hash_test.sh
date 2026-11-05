#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build an isolated test; run it explicitly only on the matching kernel.
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:-$P/artifacts/wifi-forward-hash-test}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
cp "$P/tools/test_wifi_forward_hash.c" "$OUT/"
python3 - "$KERNEL/net/mac80211/tx.c" "$P/port/overlay/target/linux/rtl9617c/patches-6.18/219-mac80211-forward-hash.patch" "$OUT" <<'PY'
from pathlib import Path
import subprocess
import sys
import tempfile
source, patch, out = map(Path, sys.argv[1:])
with tempfile.TemporaryDirectory(prefix='forward-hash-source-') as directory:
    work = Path(directory)
    target = work / 'net/mac80211/tx.c'
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    command = ['patch', '-p1', '--fuzz=0', '-i', str(patch)]
    if subprocess.run(command + ['-R', '--dry-run'], cwd=work, capture_output=True).returncode:
        subprocess.run(command, cwd=work, check=True)
    text = target.read_text()
    start = text.index('static void ieee80211_prepare_forward_hash(')
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    (out / 'forward-hash-under-test.h').write_text(text[start:end] + '\n')
    tx = text[text.index('void __ieee80211_subif_start_xmit('):]
    assert tx.index('ieee80211_prepare_forward_hash(skb);') < tx.index('ieee80211_lookup_ra_sta(')
PY
printf 'obj-m := test_wifi_forward_hash.o\nccflags-y += -Wno-unused-function\n' > "$OUT/Makefile"
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH" STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- modules
