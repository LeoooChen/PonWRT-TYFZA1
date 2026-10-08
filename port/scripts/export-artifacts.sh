#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Export only verified local boot-test artifacts; never install or flash them.
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
PORT=$(cd -- "$(dirname -- "$0")/.." && pwd)
exec 9>"$PORT/../artifacts/openwrt-world.lock"
flock 9
TREE=$(realpath "${1:?Usage: export-artifacts.sh openwrt-tree stock-dtb new-output-dir}")
STOCK=$(realpath "${2:?Specify the identified TYFZA1 backup DTB}")
OUT=${3:?Specify a new output directory}
[ ! -e "$OUT" ] || { echo "Output already exists: $OUT" >&2; exit 1; }
BIN=$TREE/bin/targets/rtl9617c/generic
# Do not export the partial output of a failed world build.
test -s "$BIN/profiles.json" && test -s "$BIN/sha256sums"
(cd "$BIN" && sha256sum -c sha256sums)
mkdir -p "$OUT/firmware" "$OUT/source" "$OUT/verification"
OUT=$(realpath "$OUT")
python3 "$PORT/tools/verify_artifacts.py" "$TREE" "$STOCK" \
    --report "$OUT/verification/artifacts.json"
python3 -m unittest discover -s "$PORT/tools" -p 'test_*.py' -v \
    > "$OUT/verification/host-tests.log" 2>&1
find "$BIN" -maxdepth 1 -type f -exec cp -t "$OUT/firmware" -- '{}' +
cp "$TREE/.config" "$OUT/verification/openwrt.config"
KERNEL=$(python3 "$PORT/tools/project_paths.py" --kernel-directory "$TREE")
cp "$KERNEL/.config" \
    "$OUT/verification/kernel.config"
git -C "$TREE" diff -- tools/fakeroot/Makefile > "$OUT/verification/applied-host.patch"
# Include the port sources and build recipe, never reference firmware or keys.
tar -C "$PORT/.." --exclude='__pycache__' --exclude='*.pyc' -cf - \
    port scripts tools docs manifests README.md evidence/rtl-research/LICENSE \
    evidence/rtl-research/data/receipts/tyfza1-stock-layout.json \
    evidence/rtl-research/data/receipts/tyfza1-stock-volume-ids.json \
    evidence/rtl-research/data/receipts/tyfza1-boot-compression.json \
    evidence/rtl-research/data/receipts/tyfza1-public-signing-key-check.json \
    evidence/rtl-research/data/receipts/tyfza1-legacy-key-algorithm.json \
    evidence/rtl-research/data/receipts/tyfza1-original-fit-verified.json \
    | tar -C "$OUT/source" -xf -
python3 - "$PORT/sources.lock.json" "$OUT/README.txt" <<'PY'
import json,sys
from pathlib import Path
source=json.loads(Path(sys.argv[1]).read_text())['openwrt']
Path(sys.argv[2]).write_text('TYFZA1 / RTL9617C native OpenWrt RAM-test artifacts.\n'
    f"OpenWrt {source['commit']}; Linux {source['kernel']}.\n"
    'Read source/port/README.md and source/docs/ for build and validation records.\n'
    'This export contains the current adaptation, recipes and raw images.\n'
    'Signed FIT sets are generated separately; persistent UBI installation is still under development.\n')
PY
(cd "$OUT" && find firmware source verification -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
echo "Exported offline-verified BOOT-TEST artifacts to $OUT"
