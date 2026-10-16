#!/usr/bin/env python3
"""Record native two-context timer IO; this is not a hardware safety verdict."""
from pathlib import Path
import hashlib
import json
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC

binary = (Path(sys.argv[1]) / 'pe-tx.bin').read_bytes()
BASE, CONTROL, A, B, TIMER = 0x4000000, 0x4002000, 0x4102000, 0x4202000, 0x100290b0
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
for at, size in ((0x1fc00000, 0x4000), (BASE, 0x20000), (A - 0x2000, 0x20000),
                 (B - 0x2000, 0x20000), (0x10020000, 0x10000)):
    u.mem_map(at, size)
u.mem_write(0x1fc00000, binary)


def put(at, value): u.mem_write(at, struct.pack('<I', value))
def get(at): return struct.unpack('<I', u.mem_read(at, 4))[0]


for slot, mb in enumerate((A, B)):
    put(CONTROL + (140 + slot) * 4, mb | 0xa0000000)
    put(mb, 0x50455744)
    put(mb + 64 * 4, TIMER | 0xa0000000)
put(A + 3 * 4, 2)
put(A + 67 * 4, 1)
put(B + 3 * 4, 1)
writes = []


def timer_write(uc, access, at, size, value, context):
    writes.append({'offset': at - TIMER, 'value': value,
                   'A_pause_ack': get(A + 4 * 4), 'B_ack': get(B + 4 * 4)})


u.hook_add(UC_HOOK_MEM_WRITE, timer_write, begin=TIMER, end=TIMER + 35)
u.reg_write(UC_MIPS_REG_A0, CONTROL | 0xa0000000)
u.reg_write(UC_MIPS_REG_SP, (BASE + 0x1fff0) | 0xa0000000)
pc = 0xbfc00400


def run():
    global pc
    u.emu_start(pc, 0xbfc03ffc, count=10000)
    pc = u.reg_read(UC_MIPS_REG_PC)


run()
assert get(A + 4 * 4) == 2
paused_writes = list(writes)
assert all(v['A_pause_ack'] == 2 for v in paused_writes)
pending_at_pause = get(A + 67 * 4) != get(A + 69 * 4)
writes.clear()
# The second, running radio must still be able to notify ARM.
put(B + 67 * 4, 1)
run()
assert len(writes) == 5 and get(B + 69 * 4) == 1
other_radio_writes = list(writes)
writes.clear()
# Model the existing IRQ handler's acknowledgements, then resume radio A.
for mb in (A, B): put(mb + 66 * 4, get(mb + 69 * 4))
put(A + 3 * 4, 3)
if not pending_at_pause:
    put(A + 67 * 4, get(A + 67 * 4) + 1)
run()
assert len(writes) == 5 and get(A + 69 * 4) == get(A + 67 * 4)
print(json.dumps({'PE_sha256': hashlib.sha256(binary).hexdigest(),
                  'timer_writes_after_pause_ack': paused_writes,
                  'running_second_radio_can_notify': len(other_radio_writes) == 5,
                  'resume_delivers_notification': len(writes) == 5,
                  'pending_at_pause_delivered_without_new_event': pending_at_pause and len(writes) == 5,
                  'scope': 'Actual linked shared PE dispatcher and timer writes. IRQ acknowledgements '
                           'are modeled; no physical IRQ latency, resource-release interleaving '
                           'or hardware test. Timer writes alone do not prove a bug.'}, indent=2))
