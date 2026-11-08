#!/usr/bin/env python3
"""Exercise NI request construction for every TID and scheduler starting point."""
from pathlib import Path
import json
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
fixture = root / 'scripts/test_pe_ps_selection.py'
ns = {'__file__': str(fixture)}
exec(compile(fixture.read_text().split('\nrows = []')[0], str(fixture), 'exec'), ns)
from unicorn import UC_HOOK_MEM_READ
BASE, MB = ns['BASE'], ns['MB']
channels = (0, 1, 1, 0, 2, 2, 3, 3)
cases = 0
for tid in range(8):
    for start in range(8):
        machine = ns['ns']['Radio']()
        machine.packets([1] * 7)
        machine.put(BASE + 0xe810, 0)
        machine.put(BASE + 0xe204, 10896 << 18)
        machine.put(BASE + 0x39004, 7 << 16 | 10896)
        ns['call'](machine, 'ni_ingest')
        machine.clock(101)
        # Move the actual admitted FIFO into the target TID.
        for offset in (0x13000, 0x15000, 0x16000, 0x18000):
            machine.put(BASE + offset + (8 + tid) * 4,
                        machine.get(BASE + offset + 8 * 4))
        machine.u.mem_write(BASE + 0x3a000 + (8 + tid) * 16,
                            bytes(machine.u.mem_read(BASE + 0x3a080, 16)))
        machine.put(BASE + 0x19604, 1 << tid)
        machine.put(MB + 112 * 4, start)
        machine.put(BASE + 0x12000 + (8 + tid) * 4, 64)
        machine.put(BASE + 0xd020, 0xa5a55a5a)
        machine.put(BASE + 0xd024, 0xdeadbeef)
        dead_reads = []
        machine.u.hook_add(UC_HOOK_MEM_READ,
            lambda uc, access, address, size, value, data: dead_reads.append(address),
            begin=BASE + 0xd020, end=BASE + 0xd027)
        assert ns['call'](machine, 'ni_select') == 7
        assert not dead_reads, 'locally selected ownership fields read from peer template'
        assert machine.get(BASE + 0x7000) == 256
        assert machine.get(BASE + 0x7004) == channels[tid]
        assert machine.get(BASE + 0x7010) & 7 == tid
        assert machine.get(BASE + 0x7008) >> 24 == 7
        assert machine.get(BASE + 0x11804) == 0 and machine.get(MB + 30 * 4) == 1
        assert machine.get(BASE + 0x20000) == ns['ns']['PAYLOAD']
        cases += 1
print(json.dumps({'TID_order_cases': cases, 'dead_template_reads': 0,
                  'local_owner_channel_and_complete_aggregate': True}, indent=2))
