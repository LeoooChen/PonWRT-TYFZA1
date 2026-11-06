#!/usr/bin/env python3
"""Execute original multicast NI lookup and L2 arbitration table IO."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_READ
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X7


def hash_recipes(module, ni_module):
    """Run FC group Hash add/update and the actual normal-action converter."""
    captured = []
    failure = 0
    def boundary(name, values):
        if name == 'rtk_fc_igmp_mcExtraFlowIdxTbl_find':
            return oracle.scratch + 0x2000
        if name == 'rtk_fc_mc_findKnownGroupEntry':
            return 0
        if name in ('aal_hash_action_update', 'aal_hash_add'):
            pointer = values[1] if name == 'aal_hash_action_update' else values[2]
            packed.uc.mem_write(packed.scratch, bytes(oracle.uc.mem_read(pointer, 147)))
            packed.call('convert_act_flow_nomal_mode', packed.scratch, packed.scratch + 0x1000)
            captured.append(dict(function=name, words=list(packed.unpack(packed.scratch + 0x1000, '8I'))))
            if name == 'aal_hash_add':
                oracle.uc.mem_write(oracle.uc.reg_read(UC_ARM64_REG_X7), struct.pack('<I', 123))
            return failure
        raise AssertionError(('unmodeled group Hash boundary', name, values))
    packed = ModuleOracle(ni_module, ['convert_act_flow_nomal_mode'], boundary)
    oracle = ModuleOracle(module, ['rtk_fc_syncToHw_G3updateGroupHash', 'rtk_fc_syncToHw_G3addGroupFwdHash'], boundary)
    pointer = oracle.scratch + 0x1000
    oracle.word(oracle.symbols['fc_db'][0] + 0x200000 + 384, pointer)
    oracle.uc.mem_write(pointer + 8, struct.pack('<I', 1))
    recipe = pointer + 0x100
    # add: packet/context pointers and its IPv4 header descriptor.
    source, context, packet = pointer + 0x400, pointer + 0x600, pointer + 0x800
    oracle.word(source + 32, packet)
    oracle.word(context + 32, packet)
    oracle.uc.mem_write(packet + 55, b'\6')
    results = []
    for group in (1, 63, 255):
        oracle.uc.mem_write(recipe, bytes(24))
        oracle.uc.mem_write(recipe + 8, struct.pack('<I', group))
        oracle.uc.mem_write(recipe + 20, struct.pack('<H', 96))
        for failure in (0, 5):
            captured.clear()
            assert oracle.call('rtk_fc_syncToHw_G3updateGroupHash', 0, 123, recipe) == 0
            assert oracle.call('rtk_fc_syncToHw_G3addGroupFwdHash', source, context, 0, 0, pointer + 64, group, 0xffffffff, 96) == 0
            assert len(captured) == 2 and captured[0]['words'] == captured[1]['words']
            words = captured[0]['words']
            assert words[0] & 0x7ffff == (1 << 18) | group << 8 | 0x70
            results.append(dict(group=group, injected_hash_error=failure, returned=0, captured=list(captured)))
    return dict(cases=results, findings=[
        'FC group add/update sets MC=1, MCGID=NI group index, DPID valid/priority/permit=1, DEEPQ=0 before replication.',
        'Both wrappers return0 even when the modeled original Hash add/update callee returns5; success does not establish publication.'])


def action_recipes(module):
    """Run the actual port-action producer; model only its allocation boundary."""
    events = []
    def boundary(name, values):
        if name == 'aal_l2_vlan_default_cfg_get':
            # The producer's four TPID candidates are output offsets12..18.
            oracle.uc.mem_write(values[1], bytes(24))
            oracle.uc.mem_write(values[1] + 12, struct.pack('<4H', 0x8100, 0x88a8, 0x9100, 0x9200))
            return 0
        if name == 'aal_l2fe_pe_my_mac_add':
            events.append(dict(operation='mac', bytes=bytes(oracle.uc.mem_read(values[2], 6)).hex()))
            oracle.uc.mem_write(values[1], struct.pack('<H', 3))
            return 0
        if name in ('generic_entry_add', 'generic_entry_get'):
            assert values[0] == oracle.symbols['l2fe_arb_fib_table'][0]
            events.append(dict(operation='action', words=list(oracle.unpack(values[1], 'III'))))
            oracle.uc.mem_write(values[2], struct.pack('<I', 7))
            return 0
        if name == 'printk':
            events.append(dict(operation='log'))
            return 0
        raise AssertionError(('unmodeled multicast action boundary', name, values))
    oracle = ModuleOracle(module, ['_aal_arb_mc_fib_action_add', '_aal_arb_mc_fibIdx_get'], boundary)
    oracle.word(oracle.symbols['l2fe_arb_fib_table'][0] + 136, 1)
    pointer = oracle.scratch + 0x1000
    cases = []
    for destination in (0, 6, 14, 32):
        for tag in (False, True):
            for swap in (False, True):
                recipe = bytearray(42)
                recipe[0] = destination
                # Original generic action layout: MAC command bit11, then
                # outer/S-tag command at11, VID12, TPID-command14/TPID16.
                if swap:
                    struct.pack_into('<H', recipe, 2, 1 << 11)
                    recipe[4:10] = bytes.fromhex('665544332202')
                if tag:
                    recipe[11] = recipe[14] = 1
                    struct.pack_into('<H', recipe, 12, 4095)
                    struct.pack_into('<H', recipe, 16, 0x8100)
                oracle.uc.mem_write(pointer, bytes(recipe))
                events.clear()
                assert oracle.call('_aal_arb_mc_fib_action_add', pointer + 64, pointer) == 0
                assert oracle.unpack(pointer + 64, 'H')[0] == 7
                words = next(event['words'] for event in events if event['operation'] == 'action')
                expected = [0, destination << 24 | 1 << 30, 0]
                if tag:
                    expected[1] |= 1 | (4095 << 2) | (1 << 14)
                if swap:
                    expected[2] = 0x402 | (3 << 2)
                    assert events[0] == dict(operation='mac', bytes='022233445566')
                assert words == expected, (destination, tag, swap, words, expected)
                cases.append(dict(destination=destination, push_outer_4095=tag, source_mac_swap=swap,
                    input=recipe.hex(), words=words, events=list(events)))
    return cases


def group_lifecycle(module):
    """Run original group bookkeeping with explicit refcounted table storage."""
    events = []
    tables = {'ni': {}, 'action': {}}
    depth = 0
    fault = None
    def boundary(name, values):
        nonlocal depth
        if name == 'ca_spin_lock': depth += 1; events.append(dict(operation='lock', depth=depth)); return 0
        if name == 'ca_spin_unlock': depth -= 1; assert depth >= 0; events.append(dict(operation='unlock', depth=depth)); return 0
        if name == 'printk': return 0
        if name == 'aal_l2_vlan_default_cfg_get': oracle.uc.mem_write(values[1], bytes(24)); return 0
        if name.startswith('generic_entry_'):
            kind = 'ni' if values[0] == ni else 'action'
            assert values[0] in (ni, action)
            if name == 'generic_entry_add':
                content = bytes(oracle.uc.mem_read(values[1], 8 if kind == 'ni' else 12))
                found = next((i for i, (row, count) in tables[kind].items() if row == content), None)
                index = found if found is not None else next(i for i in range(1 if kind == 'ni' else 0, 256 if kind == 'ni' else 64) if i not in tables[kind])
                events.append(dict(operation='add', table=kind, index=index, content=content.hex()))
                if fault == ('add', kind): return 5
                tables[kind][index] = content, tables[kind][index][1] + 1 if found is not None else 1
                oracle.uc.mem_write(values[2], struct.pack('<I', index)); return 0
            index = values[1]
            events.append(dict(operation=name.removeprefix('generic_entry_'), table=kind, index=index))
            if fault == (name.removeprefix('generic_entry_'), kind): return 5
            if name == 'generic_entry_get':
                content = bytes(oracle.uc.mem_read(values[1], 12))
                found = next((i for i, (row, count) in tables[kind].items() if row == content), None)
                if found is None: return 3
                oracle.uc.mem_write(values[2], struct.pack('<I', found)); return 0
            if index not in tables[kind]: return 3
            content, count = tables[kind][index]
            if name == 'generic_entry_get_by_idx': oracle.uc.mem_write(values[2], content); return 0
            if name == 'generic_entry_del':
                if count == 1: del tables[kind][index]
                else: tables[kind][index] = content, count - 1
                return 0
        raise AssertionError(('unmodeled multicast group boundary', name, values))
    names = ['aal_sw_mcgid_alloc', 'aal_port_forward_action_add', 'aal_port_forward_action_del',
        'aal_hw_mcgid_new', 'aal_hw_mcgid_free', '_aal_arb_mc_fib_action_add',
        '_aal_arb_mc_fib_action_del', '_aal_arb_mc_fibIdx_get']
    oracle = ModuleOracle(module, names, boundary)
    ni = oracle.symbols['ni_mce_indx_table'][0]
    action = oracle.symbols['l2fe_arb_fib_table'][0]
    oracle.word(ni + 136, 1); oracle.word(action + 136, 1)
    pointer = oracle.scratch + 0x1000
    software = oracle.symbols['swMcgid'][0]
    results = []
    def invoke(name, *args):
        events.clear()
        result = oracle.call(name, *args)
        current, previous, bitmap = struct.unpack('<HHQ', oracle.uc.mem_read(software + 2, 12))
        results.append(dict(function=name, result=result, current=current, previous=previous,
            bitmap=hex(bitmap), lock_depth=depth, events=list(events)))
        return result
    assert invoke('aal_sw_mcgid_alloc', pointer + 64) == 0
    assert oracle.unpack(pointer + 64, 'H')[0] == 0
    for destination in (6, 1):
        oracle.uc.mem_write(pointer, bytes([destination]) + bytes(41))
        assert invoke('aal_port_forward_action_add', 0, pointer) == 0
    assert invoke('aal_hw_mcgid_new', 0, pointer + 64) == 0
    old = oracle.unpack(pointer + 64, 'H')[0]
    assert tables['ni'][old][0] == struct.pack('<Q', 3)
    oracle.uc.mem_write(pointer, b'\2' + bytes(41))
    assert invoke('aal_port_forward_action_add', 0, pointer) == 0
    assert invoke('aal_hw_mcgid_new', 0, pointer + 64) == 0
    new = oracle.unpack(pointer + 64, 'H')[0]
    assert new != old and tables['ni'][new][0] == struct.pack('<Q', 7)
    assert invoke('aal_hw_mcgid_free', 0, old) == 0
    assert old not in tables['ni'] and all(count == 1 for _,count in tables['action'].values())
    # The original free drops action references before deleting the bitmap.
    # A failed NI deletion still clears its software pointer and returns0.
    fault = ('del', 'ni')
    assert invoke('aal_hw_mcgid_free', 0, new) == 0
    assert new in tables['ni'] and not tables['action']
    assert results[-1]['current'] == 65535
    assert depth == 0
    # Successful port removal has a separate missing-unlock path.
    oracle.uc.mem_write(software, bytes(14))
    tables['ni'].clear(); tables['action'].clear(); fault = None
    assert invoke('aal_sw_mcgid_alloc', pointer + 64) == 0
    oracle.uc.mem_write(pointer, b'\6' + bytes(41))
    assert invoke('aal_port_forward_action_add', 0, pointer) == 0
    assert invoke('aal_port_forward_action_del', 0, pointer) == 0
    assert depth == 1 and not tables['action']
    return dict(cases=results, findings=[
        'Group bitmap bits reference arbitration action indices; publishing a changed group allocates a different NI entry and retains the previous index until freed.',
        'Original hardware free deletes action references before its NI bitmap; NI deletion failure is ignored and software ownership is cleared while the bitmap remains.',
        'Successful aal_port_forward_action_del returns without balancing its ca_spin_lock callback; lock primitive behavior itself is not executed.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True, type=Path)
    parser.add_argument('--fc', type=Path, help='Original fc_8277c.ko for group Hash producer checks')
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
        descriptors=descriptors, lookup=lookup, action_recipes=action_recipes(args.module),
        group_lifecycle=group_lifecycle(args.module),
        hash_recipes=hash_recipes(args.fc, args.module) if args.fc else None,
        fc_sha256=hashlib.sha256(args.fc.read_bytes()).hexdigest() if args.fc else None,
        arb_fields=[dict(mask_bit=i, offset=o, width=w) for i,(o,w) in enumerate(fields)],
        failures=failures, findings=[
            'NI lookup validates 0..1023 but commands retain only 8 index bits: 0/256/512/768 alias in the original writer and reader.',
            'The original generic NI table descriptor limits allocation to256 entries, while the separate software group namespace holds1024 groups; do not confuse software IDs with hardware table indices.',
            'NI writes low/high bitmap at aa6c/aa68 then aa64 command; persistent busy returns5 after5002 reads.',
            'L2 arbitration multicast FIB is64 entries with three32-bit words at1640/163c/1638; 27 mask fields preserve unselected bits.',
            'Original arbitration setter logs both persistent read/write timeouts and still returns0; native code must propagate timeout and verify before admission.'],
        scope='Actual original ARM64 module functions with modeled indirect-engine completion/MMIO, table allocation, Hash publication and locks. Does not execute physical replication, packet edits, admission or retirement.'), indent=2) + '\n')
    print('PASS: original NI multicast index aliasing, arbitration27-field packing and asymmetric timeout behavior')


if __name__ == '__main__':
    main()
