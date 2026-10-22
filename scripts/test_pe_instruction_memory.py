#!/usr/bin/env python3
"""Execute the linked boot entry with the board DSP's CP0/CP3 operations."""
from pathlib import Path
import hashlib, json, re, struct, subprocess, sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE
from unicorn import mips_const as regs

binary = (Path(sys.argv[1]) / 'pe-tx.bin').read_bytes()
relocations = subprocess.check_output(['llvm-readelf-18', '-r', str(Path(sys.argv[1]) / 'pe-wd.o')], text=True)
assert not re.search(r'R_MIPS_(?:GOT|CALL16|GPREL)', relocations)
for dma in (0x04000000, 0x07ae0000):
    code = bytearray(binary)
    address = (dma + 0x2000) | 0xa0000000
    for offset, value in ((0, address >> 16), (4, address & 65535), (8, (dma | 0x80000000) >> 16)):
        word = struct.unpack_from('<I', code, offset)[0]
        struct.pack_into('<I', code, offset, word | value)
    cpu = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, size in ((0x1fc00000, 0x10000), (0x0fc00000, 0x10000), (dma, 0x20000)):
        cpu.mem_map(at, size)
    # The reset and28-bit execution ATU entries resolve to the same image.
    for at in (0x1fc00000, 0x0fc00000):
        cpu.mem_write(at, bytes(code))
    cp0, cp3, fills = {12: 0x400000, 20: 0}, {}, []

    def step(uc, at, size, unused):
        ins = struct.unpack('<I', uc.mem_read(at & 0x1fffffff, 4))[0]
        op, rs, rt, rd = ins >> 26, (ins >> 21) & 31, (ins >> 16) & 31, (ins >> 11) & 31
        reg = getattr(regs, 'UC_MIPS_REG_' + str(rt))
        if op == 16 and rs in (0, 4):
            if rs == 0:
                uc.reg_write(reg, cp0[rd])
            else:
                value = uc.reg_read(reg)
                if rd == 20 and value & 16 and not cp0[20] & 16:
                    assert cp3[0] == 0x0fc00000 and cp3[1] == 0x0fc07fff
                    assert bytes(uc.mem_read(cp3[0], len(code))) == code
                    fills.append((cp3[0], cp3[1]))
                cp0[rd] = value
            uc.reg_write(regs.UC_MIPS_REG_PC, at + 4)
        elif op == 19 and rs == 4:
            cp3[rd] = uc.reg_read(reg)
            uc.reg_write(regs.UC_MIPS_REG_PC, at + 4)

    cpu.hook_add(UC_HOOK_CODE, step)
    cpu.emu_start(0xbfc00000, 0x8fc00400, count=200)
    assert cpu.reg_read(regs.UC_MIPS_REG_PC) == 0x8fc00400
    assert len(fills) == 1 and cp0[20] & 0x440 == 0x440
    assert cp3[4] == dma + 0xe000 and cp3[5] == dma + 0xffff
    assert cp3[4] <= cpu.reg_read(regs.UC_MIPS_REG_SP) & 0x0fffffff <= cp3[5]
print(json.dumps({'PE_sha256': hashlib.sha256(binary).hexdigest(),
                  'linked_entry_executed': True, 'IMEM_fill_before_execution': True,
                  'IMEM_bytes': 32768, 'DMEM_bytes': 8192,
                  'no_GOT_CALL16_or_GPREL_relocations': True,
                  'scope': 'Actual linked entry; CP0/CP3 register effects and ATU aliases modeled. No hardware latency or throughput claim.'}, indent=2))
