#!/usr/bin/env python3
"""Execute linked PE PS expiry, bounded admission turns and full TSF wraps."""
from pathlib import Path
import subprocess
import sys

fixture = Path(__file__).with_name('test_pe_ps_budget.py')
scope = {'__file__': str(fixture)}
source = fixture.read_text().split('# A single sleeping peer cannot keep')[0]
exec(compile(source, str(fixture), 'exec'), scope)
Radio = scope['Radio']
BASE, MB, BAR, PAYLOAD = [scope[name] for name in ('BASE', 'MB', 'BAR', 'PAYLOAD')]
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                               UC_MIPS_REG_SP, UC_MIPS_REG_RA)


def tsf(radio, value):
    radio.put(BAR + 0xc438, value & 0xffffffff)
    radio.put(BAR + 0xc43c, value >> 32)


# A packet arriving between watchdog turns must get its full age.
r = Radio()
entered = 101_999_999
tsf(r, entered)
r.packets([1])
r.run()
tsf(r, 110_000_000)
r.run()
assert not r.returns and r.get(BASE + 0x11804) == 1, 'stale watchdog timestamp expired a young packet'
tsf(r, entered + 10_000_000 - 1)
r.run()
assert not r.returns
tsf(r, entered + 10_000_000)
r.run()
assert len(r.returns) == 1 and r.get(BASE + 0x11804) == 0

# A low-word wrap, several low-word periods, the maximum valid PS interval,
# and a complete64-bit wrap all retain a packet until its actual deadline.
for entered, timeout in ((0xfffffff0, 10), (3 << 32 | 123, 13421),
                         (8 << 32 | 456, 8795824), ((1 << 64) - 5_000_000, 10)):
    r = Radio()
    r.put(BASE + 0x19804, timeout)
    tsf(r, entered)
    r.packets([1])
    r.run()
    for age in (1_000_000, timeout * 1_000_000 - 1):
        tsf(r, (entered + age) & ((1 << 64) - 1))
        r.run()
        assert not r.returns, (entered, timeout, age)
    tsf(r, (entered + timeout * 1_000_000) & ((1 << 64) - 1))
    r.run()
    assert len(r.returns) == 1

# Advancing TSF inside one admission turn may shift an individual frame's
# age by that bounded turn, without losing seconds to watchdog scheduling.
r = Radio()
tsf(r, 1_000_000)
r.packets([1, 1])


def next_enqueue(uc, access, address, size, value, unused):
    tsf(r, 1_000_037)


r.u.hook_add(UC_HOOK_MEM_WRITE, next_enqueue, begin=PAYLOAD + 12, end=PAYLOAD + 15)
r.run()
assert not r.returns and r.get(BASE + 0x11804) == 2
tsf(r, 1_000_000 + 10_000_000 - 1)
r.run()
assert not r.returns
tsf(r, 1_000_037 + 10_000_000)
r.run()
assert len(r.returns) == 2

# Force the low TSF word to roll between the high and low register reads.
r = Radio()
tsf(r, 0xfffffff0)
r.packets([1])


def roll_low(uc, access, address, size, value, unused):
    tsf(r, 1 << 32 | 16)
    r.u.hook_del(hook)


hook = r.u.hook_add(UC_HOOK_MEM_READ, roll_low, begin=BAR + 0xc438, end=BAR + 0xc43b)
r.run()
assert not r.returns and r.get(BASE + 0x11804) == 1, 'torn TSF sample expired a new packet'
tsf(r, (1 << 32 | 16) + 10_000_000)
r.run()
assert len(r.returns) == 1

# One selection turn shares a fresh sample across sleeping queue heads;
# awake large-frame selection incurs no PS clock IO.
directory = Path(sys.argv[1])
symbols = {parts[2]: int(parts[0], 16)
           for line in subprocess.check_output(['llvm-nm-18', str(directory / 'pe-wd.elf')], text=True).splitlines()
           if len(parts := line.split()) == 3}
r = Radio(peers=2)
r.packets([1, 2])
r.run()
reads = []


def clock_io(uc, access, address, size, value, unused):
    reads.append(address)


def select():
    for reg, value in ((UC_MIPS_REG_A0, MB), (UC_MIPS_REG_A1, BASE),
                       (UC_MIPS_REG_A2, BASE + 0x7000), (UC_MIPS_REG_SP, BASE + 0x1fff0)):
        r.u.reg_write(reg, value | 0xa0000000)
    r.u.reg_write(UC_MIPS_REG_RA, 0x9fc01ff0)
    r.u.emu_start(symbols['ni_select'], 0x9fc01ff0, count=100000)


r.u.hook_add(UC_HOOK_MEM_READ, clock_io, begin=BAR + 0xc438, end=BAR + 0xc43f)
select()
assert len(reads) == 3 and r.get(BASE + 0x11804) == r.get(BASE + 0x11808) == 1
r.put(BASE + 0xe810, 0)
r.put(BASE + 0xe820, 0)
reads.clear()
select()
assert not reads
print('PS timestamps: fresh admission turns, bounded within-batch age error, exact deadlines, 32/64-bit wrap, max interval, torn-read retry and constant clock IO passed.')
