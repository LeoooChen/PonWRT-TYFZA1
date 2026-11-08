#!/usr/bin/env python3
"""Exercise linked NI selection across sleep transitions, with and without a grant."""
from pathlib import Path
import json
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
pe = Path(sys.argv[1])
# Reuse the existing production ownership/PS fixture, without its test cases.
source = (root / 'scripts/test_pe_ps_budget.py').read_text().split('# A single sleeping peer')[0]
ns = {'__file__': str(root / 'scripts/test_pe_ps_budget.py')}
exec(compile(source, ns['__file__'], 'exec'), ns)
from unicorn import UC_HOOK_MEM_READ
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                                UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_V0)
BASE, MB = ns['BASE'], ns['MB']
symbols = {p[2]: int(p[0], 16) for line in subprocess.check_output(
    ['llvm-nm-18', str(pe / 'pe-wd.elf')], text=True).splitlines() if len(p := line.split()) == 3}

def call(machine, name):
    for reg, value in ((UC_MIPS_REG_A0, MB), (UC_MIPS_REG_A1, BASE),
                       (UC_MIPS_REG_A2, BASE + 0x7000), (UC_MIPS_REG_SP, BASE + 0x1fff0)):
        machine.u.reg_write(reg, value | 0xa0000000)
    machine.u.reg_write(UC_MIPS_REG_RA, 0x9fc0fff0)
    machine.u.emu_start(symbols[name], 0x9fc0fff0, count=100000)
    return machine.u.reg_read(UC_MIPS_REG_V0)

def radio(asleep=False, grant=False, quota=False):
    m = ns['Radio']()
    m.packets([1, 1])
    m.put(BASE + 0xe810, int(asleep))
    if grant:
        m.put(BASE + 0xe814, 17)
        m.put(BASE + 0xe81c, 1 << 16 | 1)
        m.put(BASE + 0x19e04, 2)
    if quota:
        m.put(BASE + 0x3f000, 1)
        m.put(BASE + 0x3e810, 1)
    call(m, 'ni_ingest')
    return m

rows = []
for quota in (False, True):
    for when in range(1, 10):
        m = radio(quota=quota)
        reads = []
        def transition(uc, access, address, size, value, data):
            reads.append(address)
            if len(reads) == when:
                m.put(BASE + 0xe810, 1)
        m.u.hook_add(UC_HOOK_MEM_READ, transition, begin=BASE + 0xe810, end=BASE + 0xe813)
        selected = call(m, 'ni_select')
        owner = m.get(BASE + 0x20004)
        assert not (owner & (1 << 16)) and not m.get(MB + 39 * 4), (quota, when)
        assert not m.get(BASE + 0x1ba0c), 'quota rejection completed an unissued PS generation'
        rows.append({'quota': quota, 'sleep_on_read': when, 'selected': selected, 'sleep_reads': len(reads)})

# Changing sleep after admission must not change the classification of the
# selected aggregate or quota rejection. No false EOSP may complete a period.
for asleep in (False, True):
    for quota in (False, True):
        m = radio(asleep=asleep, grant=asleep, quota=quota)
        def after_commit(uc, access, address, size, value, data):
            m.put(BASE + 0xe810, int(not asleep))
            if asleep:
                m.put(BASE + 0xe81c, 0)
                m.put(BASE + 0x19e04, 4)
            m.u.hook_del(hook)
        hook = m.u.hook_add(UC_HOOK_MEM_READ, after_commit, begin=BASE + 0xd034, end=BASE + 0xd037)
        assert call(m, 'ni_select') == (0 if quota else 1)
        offset = 2048 * 8 if quota else 0
        assert bool(m.get(BASE + 0x20004 + offset) & (1 << 16)) == asleep
        assert m.get(MB + 39 * 4) == int(asleep)
        assert m.get(BASE + 0x1ba0c) == int(asleep and quota)
        if asleep:
            assert m.get(BASE + 0x28000 + (2048 * 4 if quota else 0)) == 17
            assert m.get(BASE + 0x7010) & (1 << 20), 'admitted U-APSD lost EOSP'
        else:
            assert not m.get(BASE + 0x28000)
            assert call(m, 'ni_select') == 0, 'next sleeping selection bypassed grant admission'

# An awake A-MSDU remains an ordinary in-flight aggregate if sleep arrives
# after its admission; its subframes cannot become a multi-frame PS grant.
m = radio()
m.put(BASE + 0x39004, 2 << 16 | 3793)
m.clock(101)
def sleep_aggregate(uc, access, address, size, value, data):
    m.put(BASE + 0xe810, 1)
    m.u.hook_del(hook)
hook = m.u.hook_add(UC_HOOK_MEM_READ, sleep_aggregate, begin=BASE + 0xd034, end=BASE + 0xd037)
assert call(m, 'ni_select') == 2
assert m.get(BASE + 0x7010) & (1 << 21)
assert not m.get(MB + 39 * 4) and not (m.get(BASE + 0x20004) & (1 << 16))

# Wake followed by sleep can leave the same state but revoke the old grant.
# Inject at the final existing command check, after its two snapshot reads.
for version in (2, 0xfffffffe):
    m = radio(asleep=True, grant=True)
    m.put(BASE + 0x19e04, version)
    reads = []
    def wake_sleep(uc, access, address, size, value, data):
        reads.append(address)
        if len(reads) == 3:
            m.put(BASE + 0xe81c, 0)
            m.put(BASE + 0x19e04, (version + 2) & 0xffffffff)
    m.u.hook_add(UC_HOOK_MEM_READ, wake_sleep, begin=BASE + 0x19e04, end=BASE + 0x19e07)
    assert call(m, 'ni_select') == 0 and m.get(MB + 39 * 4) == 0
    assert m.get(BASE + 0x11804) == 2

m = radio(asleep=True, grant=True)
assert call(m, 'ni_select') == 1 and call(m, 'ni_select') == 0
assert m.get(BASE + 0x11804) == 1 and m.get(MB + 39 * 4) == 1
print(json.dumps({'unissued_grant_race_cases': len(rows), 'post_admission_transitions': 4, 'aggregate_transition': True, 'wake_sleep_ABA': 2,
                  'one_grant_one_frame': True, 'cases': rows}, indent=2))
