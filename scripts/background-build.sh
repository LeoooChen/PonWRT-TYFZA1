#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
set -u
P=/home/leo/PonWRT-TYFZA1
cd "$P"
export TZ=Asia/Shanghai
export TYFZA1_PROXY=http://host.orb.internal:7897
status() {
    python3 - "$1" "${2:-0}" <<'STATUS'
import sys,json,datetime,os
from pathlib import Path
p=Path('/home/leo/PonWRT-TYFZA1/logs/background-status.json')
m={'state':sys.argv[1],'exit_code':int(sys.argv[2]),'updated_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'service':'ponwrt-tyfza1-build','log':'logs/background-build.log','openwrt_commit':'79bc56cb7483bc716da07c369530b792631904b4','kernel':'6.18.55'}
p.with_suffix('.tmp').write_text(json.dumps(m,indent=2)+'\n'); p.with_suffix('.tmp').replace(p)
STATUS
}
trap 'status failed $?' ERR
set -e
status downloading
mkdir -p sources/openwrt/dl artifacts/download-mirror
cp artifacts/kernel-source/linux-6.18.55.tar.xz sources/openwrt/dl/
prefetch() {
    local name=$1 url=$2 hash=$3
    if [ -f "sources/openwrt/dl/$name" ]; then return; fi
    echo "Domestic mirror: $url"
    if curl --noproxy '*' -fL --connect-timeout 15 --speed-time 60 --speed-limit 1024 --retry 2 "$url" -o "artifacts/download-mirror/$name.part"; then
        if printf '%s  %s\n' "$hash" "artifacts/download-mirror/$name.part" | sha256sum -c -; then
            mv "artifacts/download-mirror/$name.part" "sources/openwrt/dl/$name"
        fi
    fi
}
prefetch gcc-14.4.0.tar.xz https://mirrors.tuna.tsinghua.edu.cn/gnu/gcc/gcc-14.4.0/gcc-14.4.0.tar.xz 752b6f567beac83159c77a7680b1316bdd784738bff9a9d070112c09da90f6d9
prefetch binutils-2.46.1.tar.xz https://mirrors.tuna.tsinghua.edu.cn/gnu/binutils/binutils-2.46.1.tar.xz e127a709cba24c76de8936cb7083dd768f28cd37eb010492e2f19b71eb1294e4
sh port/scripts/sync-overlay.sh "$P/sources/openwrt"
cd sources/openwrt
export http_proxy=$TYFZA1_PROXY https_proxy=$TYFZA1_PROXY
export CURL_OPTIONS='--speed-time 60 --speed-limit 1024'
make defconfig
make -j2 download
status compiling
make -j2 world
cd "$P"
status packaging
BIN=$P/sources/openwrt/bin/targets/rtl9617c/generic
OUT=$P/artifacts/full-main-6.18.55
mkdir -p "$OUT/firmware"
cp -a "$BIN/." "$OUT/firmware/"
set -- "$BIN"/*-initramfs-Image
.venv/bin/python scripts/make_ram_fit.py "$1" "$BIN/openwrt-rtl9617c-generic-tianyi_tyfza1-stock-ubi-readonly.dtb" "$OUT/ram" --keys "$P/evidence/rtl-workspace/private/tyfza1-build-keys" --kernel-version 6.18.55 --cpus 4
status complete
