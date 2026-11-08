#!/usr/bin/env python3
"""Check acknowledged PE grants, new publications, and version rollover."""
from pathlib import Path
import json
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
fixture = root / 'scripts/test_pe_ps_selection.py'
ns = {'__file__': str(fixture)}
# Reuse the production ownership setup and actual linked selection caller.
exec(compile(fixture.read_text().split('\nrows = []')[0], str(fixture), 'exec'), ns)
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ

BASE, MB = ns['BASE'], ns['MB']
call, radio = ns['call'], ns['radio']
rows = []
for ack in (0, 2, 0xfffffffe):
    for asleep in (False, True):
        machine = radio(asleep=asleep, grant=True)
        machine.put(BASE + 0x19e04, ack)
        machine.put(BASE + 0xe818, ack)
        counter = {'sync': 0, 'grant_reads': 0}

        def observe_code(uc, address, size, data):
            if bytes(uc.mem_read(address & 0x1fffffff, 4)) == b'\x0f\0\0\0':
                counter['sync'] += 1

        def observe_grant(uc, access, address, size, value, data):
            counter['grant_reads'] += 1

        hooks = [machine.u.hook_add(UC_HOOK_CODE, observe_code)]
        for offset in (0xe814, 0xe81c):
            hooks.append(machine.u.hook_add(UC_HOOK_MEM_READ, observe_grant,
                         begin=BASE + offset, end=BASE + offset + 3))
        selected = call(machine, 'ni_select')
        assert selected == (0 if asleep else 1)
        assert counter == {'sync': 0, 'grant_reads': 0}, counter
        assert not machine.get(MB + 39 * 4), 'acknowledged grant consumed twice'
        rows.append(dict(ack=ack, asleep=asleep, **counter))
        for hook in hooks:
            machine.u.hook_del(hook)

        # Publishing a fresh grant must release exactly one queued frame,
        # including when the command version wraps from fffffffe to zero.
        machine.put(BASE + 0xe810, 1)
        machine.put(BASE + 0xe814, 18)
        machine.put(BASE + 0xe81c, 1 << 16 | 1)
        machine.put(BASE + 0x19e04, (ack + 2) & 0xffffffff)
        assert call(machine, 'ni_select') == 1
        assert machine.get(MB + 39 * 4) == 1
        assert call(machine, 'ni_select') == 0

for ack in (0, 0xfffffffe):
    machine = radio()
    machine.put(BASE + 0x19e04, ack)
    machine.put(BASE + 0xe818, ack)

    def publish_during_selection(uc, access, address, size, value, data):
        machine.put(BASE + 0x19e04, (ack + 2) & 0xffffffff)
        machine.put(BASE + 0xe81c, 0)
        machine.u.hook_del(hook)

    hook = machine.u.hook_add(UC_HOOK_MEM_READ, publish_during_selection,
                             begin=BASE + 0xe810, end=BASE + 0xe813)
    assert call(machine, 'ni_select') == 0
    assert machine.get(BASE + 0x11804) == 2 and machine.get(MB + 30 * 4) == 0

print(json.dumps({'acknowledged_cases': rows, 'version_changed_during_fast_path': 2,
                  'fresh_grant_exactly_once': True}, indent=2))
