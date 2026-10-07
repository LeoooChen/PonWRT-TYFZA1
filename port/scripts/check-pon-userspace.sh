#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Reuse ponwrt's protocol agents unchanged; do not run them on a PON interface.
set -eu
SOURCE=${1:?Usage: check-pon-userspace.sh source-checkout openwrt-checkout new-output-dir}
TREE=${2:?Specify the pinned OpenWrt checkout}
OUT=${3:?Specify a new output directory}
SOURCE=$(realpath "$SOURCE")
TREE=$(realpath "$TREE")
test ! -e "$OUT" || { echo "Output exists: $OUT" >&2; exit 1; }
test "$(git -C "$SOURCE" rev-parse HEAD)" = cce9d756742487b378749c606b30e13ee9b0f093
test -z "$(git -C "$SOURCE" status --porcelain --untracked-files=no)"
test "$(git -C "$TREE" rev-parse HEAD)" = c759267c92b0697a6fd6f369164a5ed4c1a6a03c
# rustc and the musl target stdlib MUST be from the same compiler distribution.
# Use a complete rustup toolchain or a matching official rustc + rust-std prefix.
rustc --version
TARGET_LIB=$(rustc --print target-libdir --target aarch64-unknown-linux-musl)
test -d "$TARGET_LIB" || { echo "Missing matching Rust musl target: $TARGET_LIB" >&2; exit 1; }
set -- "$TREE"/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
test "$#" = 1 && test -x "$1/aarch64-openwrt-linux-musl-gcc"
export PATH="$1:$PATH"
export STAGING_DIR="$TREE/staging_dir/target-aarch64_cortex-a55_musl"
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=aarch64-openwrt-linux-musl-gcc
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_RUSTFLAGS='-C target-feature=+crt-static'
mkdir -p "$OUT"
OUT=$(realpath "$OUT")
export CARGO_TARGET_DIR="$OUT/target"
for package in airoha-pon-daemons airoha-ponctl; do
    cd "$SOURCE/$package/src"
    cargo test --locked
    cargo build --locked --release --target aarch64-unknown-linux-musl
done
mkdir "$OUT/bin"
for binary in airoha-pond ponctl; do
    cp "$CARGO_TARGET_DIR/aarch64-unknown-linux-musl/release/$binary" "$OUT/bin/"
    aarch64-openwrt-linux-musl-readelf -h -l "$OUT/bin/$binary"
done
cp "$SOURCE/airoha-pon-daemons/src/LICENSE" "$OUT/LICENSE"
git -C "$SOURCE" archive HEAD | gzip -n > "$OUT/pon-userspace-source.tar.gz"
(cd "$OUT" && sha256sum bin/* pon-userspace-source.tar.gz LICENSE > SHA256SUMS)
echo "Cross-build only. RTL9617C hardware backend and PON service are NOT validated."
