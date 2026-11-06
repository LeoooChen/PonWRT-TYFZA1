#!/usr/bin/env python3
"""Execute original multicast NI lookup and L2 arbitration table IO."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_READ
from arm64_module_oracle import ModuleOracle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    events, messages = [], []
    pending, tables = {}, {0xaa64: {}, 0x1634: {}}
    data_regs = {0xaa64: (0xaa6c, 0xaa68), 0x1634: (0x1640, 0x163c, 0x1638)}
    stuck = False
    polls = 0
    locked = False

    def boundary(name, values):
        nonlocal locked
        if name == 'ca_spin_lock':
            assert not locked
            locked = True
            return 0
        if name == 'ca_spin_unlock':
            assert locked
            locked = False
            return 0
        if name == 'printk':
            messages.append(True)
            return 0
        raise AssertionError(('unmodeled multicast hardware boundary', name, values))

    oracle = ModuleOracle(args.module,
        ['aal_ni_mc_lkup_set', 'aal_ni_mc_lkup_get', 'aal_arb_mc_fib_set'], boundary)
    base = 0x6000000
    oracle.uc.mem_map(base, 0x10000)
    oracle.word(oracle.symbols['g_ne_reg_iobase'][0], base)
    descriptors = {}
    for name in ('ni_mce_indx_table', 'l2fe_arb_fib_table'):
        address, _ = oracle.symbols[name]
        count = oracle.unpack(address + 16, 'I')[0]
        size = oracle.unpack(address + 28, 'I')[0]
        register = oracle.unpack(address + 32, 'I')[0]
        descriptors[name] = dict(entries=count, entry_bytes=size, command_address=hex(register))
    assert descriptors['ni_mce_indx_table'] == dict(entries=256, entry_bytes=8, command_address='0xf430aa64')
    assert descriptors['l2fe_arb_fib_table'] == dict(entries=64, entry_bytes=12, command_address='0xf4301634')

    def mmio(uc, access, address, size, value, unused):
        nonlocal polls
        reg = address - base
        assert size == 4
        if access != UC_MEM_READ:
            events.append((reg, value))
            if reg in data_regs:
                pending[reg] = value
            return
        if reg not in data_regs:
            return
        polls += 1
        if reg not in pending:
            return
        command = pending[reg]
        if stuck:
            uc.mem_write(address, struct.pack('<I', command))
            return
        index = command & (255 if reg == 0xaa64 else 63)
        if command & (1 << 30):
            tables[reg][index] = [oracle.unpack(base + r, 'I')[0] for r in data_regs[reg]]
        else:
            words = tables[reg].get(index, [0] * len(data_regs[reg]))
            for r, word in zip(data_regs[reg], words):
                uc.mem_write(base + r, struct.pack('<I', word))
        uc.mem_write(address, struct.pack('<I', command & ~(1 << 31)))
        del pending[reg]

    oracle.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, mmio, begin=base, end=base + 65535)
    pointer = oracle.scratch + 0x1000
    lookup = []
    for index in (0, 1, 63, 255, 256, 511, 768, 1023, 1024):
        events.clear()
        value = 0x1020304050607080 | index
        result = oracle.call('aal_ni_mc_lkup_set', 0, index, value)
        writes = list(events)
        if index <= 1023:
            assert result == 0 and writes == [(0xaa6c, value & 0xffffffff),
                (0xaa68, value >> 32), (0xaa64, 0xc0000000 | (index & 255))]
            assert oracle.call('aal_ni_mc_lkup_get', 0, index, pointer) == 0
            assert oracle.unpack(pointer, 'Q')[0] == value
        else:
            assert result == 2 and not writes
        lookup.append(dict(index=index, result=result, writes=writes))
    # Preserve exact full-mask packing and verify every individually selected
    # field leaves all other bits intact in the original read/modify/write.
    fields = [(0, 2), (2, 12), (14, 1), (15, 1), (16, 2), (18, 3), (21, 1),
        (22, 1), (23, 9), (32, 2), (34, 12), (46, 1), (47, 2), (49, 2),
        (51, 3), (54, 1), (55, 1), (56, 6), (62, 1), (63, 1), (64, 1),
        (65, 1), (66, 3), (69, 3), (72, 2), (74, 1), (75, 1)]
    original = int.from_bytes(bytes.fromhex('a55aa55aa55aa55aa55aa55a'), 'little')
    desired = int.from_bytes(bytes.fromhex('5aa55aa55aa55aa55aa55aa5'), 'little')
    oracle.uc.mem_write(pointer, desired.to_bytes(12, 'little'))
    for bit, (offset, width) in enumerate(fields):
        events.clear()
        tables[0x1634][63] = list(struct.unpack('<III', original.to_bytes(12, 'little')))
        assert oracle.call('aal_arb_mc_fib_set', 0, 63, 1 << bit, pointer) == 0
        actual = int.from_bytes(struct.pack('<III', *tables[0x1634][63]), 'little')
        mask = ((1 << width) - 1) << offset
        assert actual == (original & ~mask) | (desired & mask), bit
        assert events[0] == (0x1634, 0x8000003f) and events[-1] == (0x1634, 0xc000003f)
        assert not locked
    events.clear()
    assert oracle.call('aal_arb_mc_fib_set', 0, 64, (1 << 27) - 1, pointer) == 2
    assert not events
    failures = []
    for name, values, expected in (
        ('aal_ni_mc_lkup_set', (0, 3, 0x1234), 5),
        ('aal_ni_mc_lkup_get', (0, 3, pointer), 5),
        ('aal_arb_mc_fib_set', (0, 3, (1 << 27) - 1, pointer), 0)):
        events.clear(); messages.clear(); pending.clear(); polls = 0; stuck = True
        result = oracle.call(name, *values, instruction_budget=1000000)
        assert result == expected and not locked
        assert polls == (20000 if name == 'aal_arb_mc_fib_set' else 5002)
        failures.append(dict(function=name, result=result, polls=polls,
            log_calls=len(messages), writes=list(events)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(module_sha256=hashlib.sha256(args.module.read_bytes()).hexdigest(),
        descriptors=descriptors, lookup=lookup, arb_fields=[dict(mask_bit=i, offset=o, width=w) for i,(o,w) in enumerate(fields)],
        failures=failures, findings=[
            'NI lookup validates 0..1023 but commands retain only 8 index bits: 0/256/512/768 alias in the original writer and reader.',
            'The original generic NI table descriptor limits allocation to256 entries, while the separate software group namespace holds1024 groups; do not confuse software IDs with hardware table indices.',
            'NI writes low/high bitmap at aa6c/aa68 then aa64 command; persistent busy returns5 after5002 reads.',
            'L2 arbitration multicast FIB is64 entries with three32-bit words at1640/163c/1638; 27 mask fields preserve unselected bits.',
            'Original arbitration setter logs both persistent read/write timeouts and still returns0; native code must propagate timeout and verify before admission.'],
        scope='Actual original ARM64 module functions with modeled indirect-engine completion/MMIO and locks. Does not prove NI bitmap destination semantics, FE/PE multicast action selection, replication, admission or retirement.'), indent=2) + '\n')
    print('PASS: original NI multicast index aliasing, arbitration27-field packing and asymmetric timeout behavior')


if __name__ == '__main__':
    main()
