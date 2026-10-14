#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
P=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:-$P/artifacts/live-radio-pe-wd}
LINKER=${TYFZA1_PE_LINKER:-ld.lld-18}
mkdir -p "$OUT"
clang-18 --target=mipsel-none-elf -march=mips32 -msoft-float -mno-abicalls -fno-pic -G0 \
    -ffreestanding -fno-builtin -fno-stack-protector -O2 ${PE_RADIO_CFLAGS:-} -Wall -Wextra -Werror \
    -c "$P/port/pe/rtl9617c-radio-wd.c" -o "$OUT/pe-wd.o"
clang-18 --target=mipsel-none-elf -march=mips32 -msoft-float -mno-abicalls -fno-pic -G0 \
    -c "$P/port/pe/rtl9617c-radio-wd-entry.S" -o "$OUT/pe-entry.o"
"$LINKER" -m elf32ltsmip -T "$P/port/pe/rtl9617c-radio-wd.ld" \
    "$OUT/pe-entry.o" "$OUT/pe-wd.o" -o "$OUT/pe-wd.elf"
llvm-objcopy-18 -O binary "$OUT/pe-wd.elf" "$OUT/pe-tx.bin"
python3 - "$OUT" <<'PY'
from pathlib import Path
import struct,sys
out=Path(sys.argv[1]);code=(out/'pe-tx.bin').read_bytes()
assert len(code)%4==0 and len(code)<=0x4000
(out/'pe-tx-code.h').write_text('static const u32 pe_tx_program[] = {\n'+
    '\n'.join('0x%08x,'%word for word in struct.unpack('<%dI'%(len(code)//4),code))+'\n};\n')
print('Native PE WD firmware:',len(code),'bytes')
PY
