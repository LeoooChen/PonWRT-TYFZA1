#!/usr/bin/env python3
"""Execute original multicast NI lookup and L2 arbitration table IO."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_READ
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4, UC_ARM64_REG_X7


def ni_multicast_init(module):
    """Execute the original NI initializer; stop at the next init stage."""
    class MulticastInitialized(Exception): pass
    base, cleared = 0x6000000, []
    def boundary(name, values):
        if name == '__ni_rxmux_init':
            return 0
        if name == 'aal_ni_mc_lkup_set':
            assert values[0] == 0 and values[2] == 0
            cleared.append(values[1])
            return 0
        if name == 'aal_ni_pkt_len_set':
            raise MulticastInitialized
        raise AssertionError(('unmodeled NI init boundary', name, values))
    ni = ModuleOracle(module, ['aal_ni_init'], boundary)
    ni.uc.mem_map(base, 0x10000)
    ni.word(ni.symbols['g_ne_reg_iobase'][0], base)
    cases = []
    for seed in (0x2f0ff, 0x12345678, 0xffffffff):
        cleared.clear()
        ni.uc.mem_write(base + 0xa004, struct.pack('<I', 2))
        ni.uc.mem_write(base + 0xaa60, struct.pack('<I', seed))
        try:
            ni.call('aal_ni_init', 0, instruction_budget=100000)
            raise AssertionError('NI multicast boundary was not reached')
        except MulticastInitialized:
            pass
        value = ni.unpack(base + 0xaa60, 'I')[0]
        assert cleared == list(range(1024))
        assert value == (seed & ~1023) | 23
        cases.append(dict(before=seed, after=value, clear_calls=len(cleared)))
    return dict(cases=cases, findings=[
        'Original aal_ni_init changes only MCE FIFO threshold bits0..9 to23; min size, source-to-POL copy and flush bits are inherited.',
        'The original initializer calls the NI clear API1024 times although its command index is8 bits. Native ownership must not clear inherited entries.'])


def arbitration_init(module, fc_module):
    """Execute original arbitration defaults and FC's subsequent overrides."""
    base = 0x6000000
    pending, tables, events = {}, {0x164c: {}, 0x1654: {}, 0x1420: {}, 0x1574: {}}, []
    def boundary(name, values):
        if name == 'ca_spin_lock_init':
            ni.word(values[0], 1)
            return 0
        if name in ('ca_spin_lock', 'ca_spin_unlock'):
            return 0
        if name == 'scfg_read':
            ni.uc.mem_write(values[3], struct.pack('<I', 0xffffffff))
            return 0
        if name == 'aal_arb_non_known_pol_map_set':
            return 0
        raise AssertionError(('unmodeled arbitration init boundary', name, values))
    ni = ModuleOracle(module, ['aal_arb_init', 'aal_arb_ctrl_set', 'aal_arb_port_dbuf_set',
        'aal_port_arb_ldpid_pdpid_map_set', 'aal_port_arb_ldpid_pdpid_map_set.part.0',
        'aal_port_elpb_cfg_set', 'aal_port_mmshp_check_set',
        'aal_port_mmshp_check_set.part.0'], boundary)
    ni.uc.mem_map(base, 0x10000)
    ni.word(ni.symbols['g_ne_reg_iobase'][0], base)
    # Reset ARB_CTRL from the matching register map; only producer-modified
    # bits are claimed by this experiment, not arbitrary inherited state.
    ni.uc.mem_write(base + 0x1600, struct.pack('<I', 0x09c71d80))
    def mmio(uc, access, address, size, value, unused):
        reg = address - base
        assert size == 4
        if access != UC_MEM_READ:
            events.append((reg, value))
            if reg in tables: pending[reg] = value
        elif reg in pending:
            command = pending.pop(reg)
            index = command & (7 if reg == 0x164c else 255)
            if reg in (0x1420, 0x1574):
                data = (0x1428,0x1424) if reg == 0x1420 else (0x157c,0x1578)
                if command & (1 << 30): tables[reg][index] = [ni.unpack(base+r,'I')[0] for r in data]
                else:
                    for r,v in zip(data,tables[reg].get(index,[0,0])):
                        uc.mem_write(base+r,struct.pack('<I',v))
            elif command & (1 << 30): tables[reg][index] = ni.unpack(base + reg + 4, 'I')[0]
            else: uc.mem_write(base + reg + 4, struct.pack('<I', tables[reg].get(index, 0)))
            uc.mem_write(address, struct.pack('<I', command & ~(1 << 31)))
    ni.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, mmio, begin=base, end=base + 65535)
    assert ni.call('aal_arb_init', 0) == 0
    assert tables[0x164c] == dict.fromkeys(range(8), 0)
    arb_control = ni.unpack(base + 0x1600, 'I')[0]
    assert arb_control == (0x09c71d80 & ~(1 << 8)) | 1 << 31 | 2
    assert ni.unpack(base + 0x160c, 'I')[0] & 1
    def fc_boundary(name, values):
        if name == 'aal_arb_port_dbuf_set':
            data = bytes(fc.uc.mem_read(values[3], 20))
            ni.uc.mem_write(ni.scratch + 0x1000, data)
            return ni.call(name, *values[:3], ni.scratch + 0x1000)
        if name == 'aal_port_arb_ldpid_pdpid_map_set':
            return ni.call(name, *values, fc.uc.reg_read(UC_ARM64_REG_X4))
        if name in ('aal_port_dpb_ctrl_set', 'aal_port_dpb_cfg_set', 'aal_fdb_lrn_fwd_ctrl_set',
            'aal_fdb_ctrl_set', 'aal_l3pp_l2tp_sport_set', 'aal_l3pp_vxlan_dport_set'):
            return 0
        raise AssertionError(('unmodeled FC L2 init boundary', name, values))
    fc = ModuleOracle(fc_module, ['rtk_8277c_asic_l2fe_init.isra.0'], fc_boundary)
    fc.call('rtk_8277c_asic_l2fe_init.isra.0')
    assert tables[0x164c] == {**dict.fromkeys(range(7), 0), 7: 1 | 1 << 8 | 17 << 9}
    assert {index: tables[0x1654][index] for index in (6, 70, 134, 198)} == {6: 6, 70: 8, 134: 6, 198: 8}
    original_dbuf=dict(tables[0x164c])
    # Execute the exact native P6 destination-match and egress low-field
    # recipes through the original setters; do not claim this is FC's default.
    pointer=ni.scratch+0x1000
    ni.uc.mem_write(pointer,struct.pack('<5I',1,1,6,0,0))
    assert ni.call('aal_arb_port_dbuf_set',0,0,31,pointer)==0
    assert tables[0x164c][0]==0x1b
    ni.uc.mem_write(pointer,struct.pack('<4I',1,1,0,0)+bytes(48))
    tables[0x1420][6]=[0x1230,0x4500]
    assert ni.call('aal_port_elpb_cfg_set',0,6,15,pointer)==0
    assert tables[0x1420][6]==[0x1233,0x4500]
    for source in (0,4,6,27,63):
        assert ni.call('aal_port_mmshp_check_set',0,source,((1<<64)-1)^(1<<source))==0
        assert tables[0x1574][source]==[
            (((1<<64)-1)^(1<<source))&0xffffffff,
            (((1<<64)-1)^(1<<source))>>32]
    return dict(arb_control=hex(arb_control), port_deep_buffer_table=original_dbuf,
        native_p6_dbuf=0x1b,native_p6_egress=tables[0x1420][6],
        port_membership_table=tables[0x1574],
        p6_destination_map={index: tables[0x1654][index] for index in (6, 70, 134, 198)},
        events=events, findings=[
            'Original ARB init clears all8 port deep-buffer rules, preserves original multicast source, uses flow-ID table selection and disables HeaderA deep-buffer gating.',
            'FC adds only source17 to port deep-buffer rule7; it does not add a P6 destination rule. P6 maps to PDPID6 without dbuf and PDPID8 with dbuf.',
            'These executed initializers do not establish that physical-port-to-deep-Q SCFG forces multicast replicas into the same queue path as unicast Hash hits.'])


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


def group_unlearn(module):
    """Follow last-reference deletion through FC's real Hash-delete wrapper."""
    events, failure = [], 0
    def boundary(name, values):
        if name == 'aal_hash_delete':
            assert values[:2] == [0, 37]
            events.append(dict(function=name, device=values[0], hash=values[1], result=failure))
            return failure
        if name == 'sprintf':
            oracle.uc.mem_write(values[0], b'error\0')
            return 5
        if name == 'printk':
            return 0
        raise AssertionError(('unmodeled group unlearn boundary', name, values))
    oracle = ModuleOracle(module, ['rtk_fc_mc_delKnownGroupEntry.part.0',
        '_rtk_fc_mc_groupToHwAsUnknown', 'rtk_8277c_flow_mcast_group_asunknown'], boundary)
    record, state, bitmap = oracle.scratch, oracle.scratch + 0x1000, oracle.scratch + 0x2000
    database = oracle.symbols['fc_db'][0]
    row = database + 0x200000 + 37 * 63
    cases = []
    for refs in (0, 1, 2):
        for failure in (0, 5):
            events.clear()
            oracle.uc.mem_write(record, bytes(64))
            oracle.uc.mem_write(record + 16, struct.pack('<H', refs))
            oracle.uc.mem_write(record + 20, struct.pack('<II', 37, 0xffffffff))
            oracle.uc.mem_write(record + 28, b'\x01')
            oracle.uc.mem_write(database + 332, struct.pack('<I', 64))
            oracle.word(database + 344, bitmap)
            oracle.word(bitmap, (1 << 64) - 1)
            oracle.word(row + 376, state)
            oracle.uc.mem_write(state, struct.pack('<I', 1))
            oracle.call('rtk_fc_mc_delKnownGroupEntry.part.0', record)
            remaining = oracle.unpack(record + 16, 'H')[0]
            known = oracle.unpack(record + 28, 'B')[0] & 1
            hash_owned = oracle.unpack(state, 'I')[0] & 1
            assert remaining == max(0, refs - 1)
            assert known == (refs != 1)
            assert hash_owned == (refs != 1 or failure != 0)
            assert len(events) == (refs == 1)
            if refs == 1 and failure:
                oracle.call('rtk_fc_mc_delKnownGroupEntry.part.0', record)
                assert len(events) == 1 and oracle.unpack(state, 'I')[0] & 1
            cases.append(dict(references=refs, hash_delete_error=failure,
                remaining_references=remaining, known_flag=known,
                hash_software_owner=hash_owned, calls=list(events)))
    return dict(cases=cases, findings=[
        'Last-reference unlearn calls only aal_hash_delete at the observed hardware boundary; it supplies no separate NI/MCE/PE retirement fence.',
        'If Hash delete fails, the lower wrapper preserves the Hash software owner, but the caller still decrements references to0 and clears its known-group flag. Repeating unlearn with0 references does nothing.',
        'Native withdrawal must retain its group/cookie and retry on Hash/cache failure instead of treating original software unlearn as a resource-reuse guarantee.'])


def cpu_receive_dispatch(module):
    """Trace the original MCE CPU distinction; do not infer it from DA alone."""
    events=[]
    def boundary(name,args):
        if name in ('_rtk_fc_rx_final_process','rtk_fc_fastfwd_directXmit'):
            pointer=args[0] if name=='_rtk_fc_rx_final_process' else args[1]
            events.append(dict(function=name,descriptor=list(oracle.unpack(pointer,'4I'))))
            return 0
        raise AssertionError(('unmodeled CPU receive boundary',name,args))
    oracle=ModuleOracle(module,['rtk_fc_skb_rx'],boundary,
        external_symbols={'cpu_number':0x5006000})
    skb,private,ha,hc,frame=[oracle.scratch+n for n in (0,0x1000,0x2000,0x2100,0x2200)]
    oracle.word(private+8,ha);oracle.word(private+16,hc);oracle.word(skb+200,frame)
    # Model synchronous dispatch (no SMP rings); the final consumer is a boundary.
    oracle.uc.mem_write(oracle.symbols['fc_mgr_db'][0]+4,struct.pack('<I',1))
    cases=[]
    for source in (0,4,27):
        for pol in (0,55,56):
            for multicast in (False,True):
                for reason in (0,4,15):
                    events.clear()
                    oracle.uc.mem_write(frame,bytes([1 if multicast else 2])+bytes(63))
                    oracle.uc.mem_write(ha,struct.pack('<II',pol<<18,source<<9|16<<3|100<<15|2<<30))
                    oracle.uc.mem_write(hc,struct.pack('<II',0x12345678,reason<<8|0xabcd<<16))
                    assert oracle.call('rtk_fc_skb_rx',0,0,skb,private)==2
                    assert len(events)==1
                    event=events[0];converted=event['descriptor'][0]>>3&255
                    mce=source==27 and multicast and pol!=0
                    expected=17 if mce and pol==56 else 15 if mce else reason
                    assert converted==expected,(source,pol,multicast,reason,event)
                    expected_call='rtk_fc_fastfwd_directXmit' if mce and pol==55 else '_rtk_fc_rx_final_process'
                    assert event['function']==expected_call,(source,pol,multicast,reason,event)
                    cases.append(dict(source=source,pol=pol,multicast=multicast,
                        input_reason=reason,**event))
    return dict(cases=cases,sha256=hashlib.sha256(module.read_bytes()).hexdigest(),findings=[
        'For multicast with HeaderA source27 and POL56, original fc_mgr converts the receive reason to17 and uses the normal receive consumer.',
        'The neighboring POL55 becomes reason15 plus interface metadata and dispatches directly; original hardware reason alone does not identify a local multicast copy.',
        'Native multicast keeps physical source0..4. It cannot borrow this source27/POL56 predicate without tracing a new unambiguous metadata producer and preserving normal misses/MTU returns.'],
        scope='54 executions of actual rtk_fc_skb_rx; final receive/direct-transmit consumers and synchronous scheduling are modeled. No assertion of packet retirement or physical HeaderA generation.')


def replica_recipes(fc_module, ni_module, destinations=(1, 6), host_copy=False, source_mac=False):
    """Execute FC replica construction through the real arbitration producer."""
    class Published(Exception): pass
    recipes, actions = [], []
    def ni_boundary(name, values):
        if name == 'aal_l2_vlan_default_cfg_get':
            # All four original L2 egress TPID defaults are8100.
            ni.uc.mem_write(values[1], bytes(12) + struct.pack('<4H', *([0x8100] * 4)) + bytes(4))
            return 0
        if name == 'aal_l2fe_pe_my_mac_add':
            assert bytes(ni.uc.mem_read(values[2],6)) == bytes.fromhex('665544332202')
            ni.uc.mem_write(values[1],struct.pack('<H',3))
            return 0
        if name == 'generic_entry_add':
            assert values[0] == ni.symbols['l2fe_arb_fib_table'][0]
            actions.append(list(ni.unpack(values[1], '3I')))
            ni.uc.mem_write(values[2], struct.pack('<I', len(actions) - 1))
            return 0
        raise AssertionError(('unmodeled replica action boundary', name, values))
    ni = ModuleOracle(ni_module, ['_aal_arb_mc_fib_action_add'], ni_boundary)
    ni.word(ni.symbols['l2fe_arb_fib_table'][0] + 136, 1)
    def boundary(name, values):
        if name == 'aal_sw_mcgid_alloc':
            fc.uc.mem_write(values[0], b'\0\0')
            return 0
        if name == 'aal_port_forward_action_add':
            recipe = bytes(fc.uc.mem_read(values[1], 42))
            recipes.append(recipe.hex())
            ni.uc.mem_write(ni.scratch + 0x1000, recipe)
            return ni.call('_aal_arb_mc_fib_action_add', ni.scratch + 0x1100, ni.scratch + 0x1000)
        if name == 'aal_hw_mcgid_new':
            fc.uc.mem_write(values[1], struct.pack('<H', 7))
            return 0
        if name == 'rtk_fc_syncToHw_G3addGroupFwdHash':
            raise Published()
        raise AssertionError(('unmodeled FC replica boundary', name, values))
    fc = ModuleOracle(fc_module, ['rtk_fc_abstrSwSyncToHw_add', 'rtk_fc_parseAbstrSwActionField',
        'rtk_fc_parseAbstrSwPattenField'], boundary)
    source, context, flow, action, member = [fc.scratch + offset for offset in (0x1000, 0x1200, 0x1400, 0x1600, 0x1800)]
    db = fc.symbols['fc_db'][0]
    fc.word(db + 0x200000 + 384, flow)
    # The original variable-size action parser advances four bytes per VLAN.
    size = fc.symbols['patternSizeMapping'][0]
    fc.uc.mem_write(size + 6, struct.pack('<H', 6))
    fc.uc.mem_write(size + 22, struct.pack('<H', 4))
    fc.uc.mem_write(size + 10, struct.pack('<H', 4))
    fc.uc.mem_write(context + 306, struct.pack('<H', 4))
    fc.word(flow + 48, action + 40)
    fc.word(action + 40, flow + 48)
    fc.word(action + 56, member + 8)
    fc.word(member + 8, action + 56)
    cases = []
    for tags in range(3):
        for destination in destinations:
            recipes.clear(); actions.clear()
            fc.uc.mem_write(action, bytes(32))
            fc.uc.mem_write(action, struct.pack('<H', (2 if source_mac else 0) | (4 if tags else 0) | (8 if tags == 2 else 0)))
            vlan_offset=6 if source_mac else 0
            if source_mac:fc.uc.mem_write(action+4,bytes.fromhex('022233445566'))
            if tags:
                fc.uc.mem_write(action + 4 + vlan_offset, struct.pack('<HH', 0x8100, 4095 | 5 << 13 | 1 << 12))
            if tags == 2:
                fc.uc.mem_write(action + 8 + vlan_offset, struct.pack('<HH', 0x8100, 100 | 3 << 13))
            fc.uc.mem_write(member, bytes([destination]) + bytes(7))
            fc.uc.mem_write(flow + 8, bytes(4))
            fc.uc.mem_write(flow + 44, bytes([host_copy]))
            fc.uc.mem_write(db + 0x200000 + 404, bytes(4))
            try:
                fc.call('rtk_fc_abstrSwSyncToHw_add', source, context, 0)
            except Published:
                pass
            else:
                raise AssertionError('FC did not reach group Hash publication')
            assert len(recipes) == len(actions) == (2 if host_copy else 1)
            host=None
            if host_copy:
                # Original extra-copy producer uses CPU18 and POL56, not an
                # ordinary CPU16 member. No VLAN pattern in this input fixture.
                host=dict(recipe=recipes.pop(0),words=actions.pop(0))
                assert host['words']==[56<<23,18<<24|1<<30,1]
            words = actions[0]
            assert (words[1] >> 24) & 63 == destination
            assert (words[1] & 3) == (1 if tags else 2)
            assert (words[0] & 3) == (1 if tags == 2 else 2)
            assert words[2] == (0x40f if source_mac else 1)  # Stock also decrements TTL with MAC edit.
            if tags:
                assert (words[1] >> 2) & 4095 == 4095
                assert (words[1] >> 19) & 7 == 5 and (words[1] >> 23) & 1 == 1
            if tags == 2:
                assert (words[0] >> 2) & 4095 == 100 and (words[0] >> 18) & 7 == 3
            cases.append(dict(output_tags=tags, destination=destination, recipe=recipes[0], words=words,host_copy=host,source_mac=source_mac))
    return dict(cases=cases, findings=[
        'The complete FC replica producer programs both S/C tag commands: present=>tag by table(1), absent=>untag(2); it is an absolute final tag recipe, not a relative push.',
        'FC explicitly disables source-port filtering for each replica and copies priority/DEI for each present tag; unchanged source MAC leaves TTL decrement disabled.'],
        scope='Executed FC member iteration, variable-size VLAN action parsing and NI action producer; group/table allocation is modeled and execution stops at Hash publication, which is checked separately.')


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
    parser.add_argument('--mgr', type=Path, help='Original fc_mgr.ko for CPU multicast receive dispatch')
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
        ni_multicast_init=ni_multicast_init(args.module),
        group_lifecycle=group_lifecycle(args.module),
        hash_recipes=hash_recipes(args.fc, args.module) if args.fc else None,
        group_unlearn=group_unlearn(args.fc) if args.fc else None,
        fc_sha256=hashlib.sha256(args.fc.read_bytes()).hexdigest() if args.fc else None,
        arbitration_init=arbitration_init(args.module, args.fc) if args.fc else None,
        replica_recipes=replica_recipes(args.fc, args.module) if args.fc else None,
        cpu_replica_recipes=replica_recipes(args.fc, args.module, (16,)) if args.fc else None,
        cpu_receive_dispatch=cpu_receive_dispatch(args.mgr) if args.mgr else None,
        host_copy_recipes=replica_recipes(args.fc, args.module, (1,6), True) if args.fc else None,
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
