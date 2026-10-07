#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
STAMP=$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)
OUT=${1:-$P/artifacts/lan-build-$STAMP}
mkdir -p "$P/artifacts/build-scripts"
SCRIPT=$P/artifacts/build-scripts/lan-$STAMP.sh
cp "$P/scripts/build_lan_ui.sh" "$SCRIPT"
# Run a saved script so later source edits cannot change the running shell's input.
systemd-run --user --unit="ponwrt-tyfza1-lan-$STAMP" \
    --property=RemainAfterExit=yes \
    --property="WorkingDirectory=$P" \
    --property="Environment=TYFZA1_LAN_OUT=$OUT" \
    --property="StandardOutput=append:$P/logs/lan-build-$STAMP.log" \
    --property="StandardError=append:$P/logs/lan-build-$STAMP.log" \
    /bin/bash "$SCRIPT"
