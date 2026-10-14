#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec 9>"$P/artifacts/openwrt-world.lock"
flock 9
KERNEL=$(python3 "$P/port/tools/project_paths.py" --kernel-directory "$P/sources/openwrt")
OUT=$P/artifacts/live-pe-packet
mkdir -p "$OUT"
clang --target=mipsel-none-elf -march=mips32 -msoft-float -c "$P/port/pe/rtl9617c-ni-rx.S" -o "$OUT/pe-rx.o"
"$P/.venv/bin/python" - "$P" "$OUT" <<'PY'
from pathlib import Path
import struct,sys
p,out=map(Path,sys.argv[1:])
sys.path.insert(0,str(p/'analysis/python-deps'))
from elftools.elf.elffile import ELFFile
with (out/'pe-rx.o').open('rb') as stream:
    elf=ELFFile(stream)
    code=elf.get_section_by_name('.text').data()
    assert not any(s.num_relocations() for s in elf.iter_sections() if s['sh_type']=='SHT_REL')
(out/'pe-rx.bin').write_bytes(code)
(out/'pe-rx-code.h').write_text('static const u32 pe_rx_program[] = {\n'+
    '\n'.join('0x%08x,'%word for word in struct.unpack('<%dI'%(len(code)//4),code))+'\n};\n')
PY
cp "$P/tools/live-pon-diag/live_pe_packet.c" "$P/tools/live-pon-diag/pe_probe.h" "$OUT/"
printf 'obj-m := live_pe_packet.o\n' > "$OUT/Makefile"
set -- "$P"/sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/bin
export PATH="$1:$PATH" STAGING_DIR="$P/sources/openwrt/staging_dir"
make -C "$KERNEL" M="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-openwrt-linux-musl- modules
