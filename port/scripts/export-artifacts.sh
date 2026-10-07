#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Export only verified local boot-test artifacts; never install or flash them.
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
PORT=$(cd -- "$(dirname -- "$0")/.." && pwd)
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
cp "$TREE/build_dir/target-aarch64_cortex-a55_musl/linux-rtl9617c_generic/linux-6.18.52/.config" \
    "$OUT/verification/kernel.config"
git -C "$TREE" diff -- tools/fakeroot/Makefile > "$OUT/verification/applied-host.patch"
# Include the port sources and build recipe, never reference firmware or keys.
tar -C "$PORT/.." --exclude='__pycache__' --exclude='*.pyc' -cf - \
    openwrt tools/collect_tyfza1_port.sh docs/tyfza1-native-pon.md docs/tyfza1-vendor-port.md docs/licenses.md LICENSE \
    data/receipts/tyfza1-stock-layout.json data/receipts/tyfza1-stock-volume-ids.json \
    data/receipts/tyfza1-boot-compression.json data/receipts/tyfza1-public-signing-key-check.json \
    data/receipts/tyfza1-legacy-key-algorithm.json data/receipts/tyfza1-vendor-study-sources.json \
    data/receipts/tyfza1-original-fit-verified.json data/receipts/tyfza1-native-port-validation.json \
    | tar -C "$OUT/source" -xf -
cat > "$OUT/README.txt" <<'TXT'
TYFZA1 / RTL9617C native OpenWrt BOOT-TEST artifacts, NOT a flashable upgrade.
OpenWrt main snapshot c759267c92b0697a6fd6f369164a5ed4c1a6a03c; Linux 6.18.52.

Read source/openwrt/README.md before any hardware operation.
Firmware is built and inspected offline. NO real TYFZA1 boot, NAND/ECC,
persistent UBI installation, LAN, optical or PON service test has been performed.

*-initramfs-Image plus *-board.dtb are for a later, validated RAM boot only.
The normal board DTB disables NAND. *-nand-readonly.dtb is a separate experiment.
Raw Image/rootfs.squashfs are NOT the paired signed FIT and verity-rootfs payloads. Do not feed them
to a vendor updater, nand write, ubiformat or sysupgrade. Upgrade hooks reject writes.
No bootloader load addresses, ECC parameters or calibration have been invented.
Disconnect all optical fibers and confirm board identity, UART electrical levels,
boot policy and all live memory reservations before executing a RAM plan.

The source directory contains the adaptation and pinned upstream build recipe,
not all upstream dependency sources. Preserve their original licenses and obtain
complete corresponding upstream sources before redistributing derived binaries.
No stock firmware, stock modules, private keys or PON daemons are included here.
The optional signed FIT sets are exported separately, without signing keys.
TXT
(cd "$OUT" && find firmware source verification -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
echo "Exported offline-verified BOOT-TEST artifacts to $OUT"
