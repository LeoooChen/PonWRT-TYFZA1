#!/usr/bin/env python3
"""Execute the original default ONU service producer and selected relay API adapters."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

from elftools.elf.elffile import ELFFile
from arm64_elf_oracle import ElfOracle


class DefaultService:
    def __init__(self, binary, fpga=False):
        self.fpga = fpga
        copies = {}
        with binary.open('rb') as stream:
            elf = ELFFile(stream)
            symbols = elf.get_section_by_name('.dynsym')
            for section in elf.iter_sections():
                if section['sh_type'] != 'SHT_RELA':
                    continue
                for relocation in section.iter_relocations():
                    if relocation['r_info_type'] == 1024:
                        symbol = symbols.get_symbol(relocation['r_info_sym'])
                        copies[symbol.name] = bytes(symbol['st_size'])
        self.events, self.fail_at, self.operations = [], 0, 0
        self.oracle = ElfOracle(binary, self.boundary, copy_symbols=copies)
        # Follow each retained executable's own operation table.
        if fpga:
            entry = struct.unpack('<Q', self.oracle.uc.mem_read(0x428818 + 24, 8))[0]
            assert entry == 0x40e00c
        else:
            entry = struct.unpack('<Q', self.oracle.uc.mem_read(0x430bd8 + 24, 8))[0]
            assert entry == 0x41243c
        self.oracle.symbols['initialize'] = entry

    def boundary(self, name, args):
        uc = self.oracle.uc
        if name == 'memset':
            uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
            return args[0]
        if name == 'snprintf':
            text = bytes(uc.mem_read(args[2], 64)).split(b'\0')[0]
            assert text == b'FTTR-100M' and args[1] == 31
            uc.mem_write(args[0], text + b'\0')
            return len(text)
        if name in ('printf', 'fflush'):
            return 0
        if name == 'plr_api_tcont_add':
            data = bytes(uc.mem_read(args[0], 40))
        elif name in ('plr_api_gem_add', 'plr_api_remote_flow_gem_add', 'plr_api_remote_flow_vlan_filter_add'):
            data = struct.pack('<QQ', *args[:2])[:16 if name == 'plr_api_gem_add' else 12]
        elif name in ('plr_api_remote_onu_mcast_enable', 'plr_api_remote_onu_uni_mcast_enable'):
            data = struct.pack('<I', args[0]) + struct.pack('<QQ', *args[1:3])[:12] + struct.pack('<I', args[3])
        elif name == 'plr_api_cfgonu_get':
            # Model only the failure-recovery owner's metadata. It is not a
            # read of real user configuration or proof of remote cleanup.
            onu = struct.unpack('<I', uc.mem_read(args[0], 4))[0]
            assert onu == self.onu
            row = bytearray(240)
            struct.pack_into('<I', row, 0, onu)
            struct.pack_into('<I', row, 168, onu)
            row[172:180] = b'TEST\0\0\0\1'
            uc.mem_write(args[0], bytes(row))
            self.events.append(dict(operation=name, onu=onu))
            return 0
        elif name in ('plr_api_onu_del', 'plr_api_onu_add'):
            self.events.append(dict(operation=name))
            return 0
        else:
            raise AssertionError(('unmodeled original boundary', name))
        self.operations += 1
        result = 17 if self.operations == self.fail_at else 0
        self.events.append(dict(operation=name, payload=data.hex(), result=result))
        return result

    def run(self, onu, slots, fail_at=0):
        self.onu, self.fail_at, self.operations = onu, fail_at, 0
        self.events.clear()
        if self.fpga:
            assert slots == 2
        else:
            self.oracle.uc.mem_write(0x430d08 + (onu - 1) * 136 + 41, bytes([slots]))
        result = self.oracle.call('initialize', onu)
        return dict(onu=onu, slots=slots, fail_at=fail_at, result=result, events=list(self.events))


def expected(onu, slots):
    events = []
    for slot in range(1, slots):
        events += [
            ('plr_api_tcont_add', struct.pack('<II', onu, slot) + b'FTTR-100M'.ljust(32, b'\0')),
            ('plr_api_gem_add', struct.pack('<4I', onu, slot, slot, 1)),
            ('plr_api_remote_flow_gem_add', struct.pack('<I4H', onu, slot, slot, 0, 0))]
        if slots > 2:
            events.append(('plr_api_remote_flow_vlan_filter_add', struct.pack('<I4H', onu, slot, slot, 1, 100 * slot)))
    events.append(('plr_api_remote_onu_mcast_enable', struct.pack('<IH2xIH2xI', onu, 1, 2, 1, 1)))
    return events


def relay_translation(binary, onu, slot, vid):
    events = []
    def hook(name, args):
        uc = oracle.uc
        if name == 'memset':
            uc.mem_write(args[0], bytes([args[1] & 255]) * args[2]); return args[0]
        if name == 'memmove':
            uc.mem_write(args[0], bytes(uc.mem_read(args[1], args[2]))); return args[0]
        if name == 'plr_gponrm_cfg_flow':
            address = bytes(uc.mem_read(args[0], 4))
            recipe = bytes(uc.mem_read(args[1], 40))
            events.append(dict(address=address.hex(), recipe=recipe.hex()))
            return 0
        if name == 'plr_gpon_maxpon_get': return 1
        if name == 'plr_gpon_maxonu_get': return 63
        if name in ('plr_gponrm_mgmt_sem_take_with_timeout', 'plr_gponrm_mgmt_sem_give'): return 0
        raise AssertionError(('unmodeled relay boundary', name))
    oracle = ElfOracle(binary, hook, ('plr_gponrm_cfg_flow', 'plr_gpon_maxpon_get', 'plr_gpon_maxonu_get',
                                    'plr_gponrm_mgmt_sem_take_with_timeout', 'plr_gponrm_mgmt_sem_give'))
    for function, data in (
            ('plr_api_remote_flow_gem_add', struct.pack('<I4H', onu, slot, slot, 0, 0)),
            ('plr_api_remote_flow_vlan_filter_add', struct.pack('<I4H', onu, slot, slot, 1, vid))):
        a, b = struct.unpack('<QQ', data.ljust(16, b'\0'))
        assert oracle.call(function, a, b) == 0
    for event, operation in zip(events, (2, 4)):
        assert bytes.fromhex(event['address']) == struct.pack('<HH', 0, onu - 1)
        recipe = bytes.fromhex(event['recipe'])
        assert struct.unpack_from('<3H', recipe) == (slot, operation, 65535)
        assert struct.unpack_from('<I', recipe, 8)[0] == 0
        assert struct.unpack_from('<H', recipe, 12)[0] == 0
        if operation == 2:
            assert recipe[16] == 1 and struct.unpack_from('<H', recipe, 18)[0] == slot
        else:
            assert recipe[34] == 1 and struct.unpack_from('<H', recipe, 36)[0] == vid
    return dict(onu=onu, slot=slot, vid=vid, translated=events)


def bridge_selection(binary):
    """Execute the actual flow admission/bridge-selection prefix, stopping before GEM inventory."""
    class Selected(Exception):
        pass
    calls = []
    count, mode = 0, 0
    def hook(name, args):
        if name == 'plr_gponrm_cfg_flow_mgmt_get': return oracle.scratch + 0x7000
        if name == 'lstCount': return count
        if name == 'plr_gponrm_cfg_bridge_mode_get': return mode
        if name == 'plr_gponrm_dft_mbs_me_id_get':
            calls.append('shared-bridge')
            oracle.uc.mem_write(args[1], struct.pack('<H', 0x8080))
            return 0
        if name == 'plr_gponrm_card_eth_uni_mbs_me_id_get_by_card_uni_id':
            calls.append('ethernet-uni-bridge')
            oracle.uc.mem_write(args[3], struct.pack('<H', 0x8081))
            return 0
        if name == 'plr_log': return 0
        if name == 'selection_complete': raise Selected()
        raise AssertionError(('unmodeled flow selector', name))
    oracle = ElfOracle(binary, hook, ('plr_gponrm_cfg_flow_mgmt_get', 'lstCount',
        'plr_gponrm_cfg_bridge_mode_get', 'plr_gponrm_dft_mbs_me_id_get',
        'plr_gponrm_card_eth_uni_mbs_me_id_get_by_card_uni_id', 'plr_log'))
    oracle.symbols['select'] = 0xaac90
    oracle.hooks[0xab024] = 'selection_complete'
    address, recipe, context = (oracle.scratch + n for n in (0x2000, 0x3000, 0x4000))
    cases = []
    for count, mode, mask, target in ((0, 0, 2, 'shared-bridge'), (1, 1, 2, 'shared-bridge'),
                                    (0, 0, 3, 'ethernet-uni-bridge'), (1, 0, 2, None)):
        calls.clear()
        oracle.uc.mem_write(address, struct.pack('<HH', 0, 5))
        data = bytearray(40)
        struct.pack_into('<3H', data, 0, 1, mask, 0)
        data[16] = 1
        oracle.uc.mem_write(recipe, bytes(data))
        oracle.uc.mem_write(context, bytes(112))
        oracle.uc.mem_write(context + 8, struct.pack('<I', 3))
        result = 0
        try:
            result = oracle.call('select', address, recipe, context)
            assert target is None and result == 35
        except Selected:
            assert target is not None and calls == [target]
        chosen_mode = oracle.uc.mem_read(context + 12, 1)[0]
        if target is not None:
            assert chosen_mode == (target == 'shared-bridge')
        cases.append(dict(existing_flows=count, current_bridge_mode=mode, recipe_mask=mask,
                          selection=target, selected_mode=chosen_mode, result=result))
    return cases


def shared_uni_selection(binary):
    """Run original Ethernet/VEIP enumeration with differing configured/reported counts."""
    calls = []
    configured, reported = {}, {}
    def hook(name, args):
        for prefix, counts in (('plr_gponrm_cfg_card_', configured), ('plr_gponrm_card_', reported)):
            for kind in ('eth', 'veip'):
                if name == prefix + kind + '_num_get': return len(counts[kind])
                if name == prefix + kind + '_uni_num_get':
                    oracle.uc.mem_write(args[2], struct.pack('<H', counts[kind][args[1]]))
                    return 0
        if name == 'plr_gponrm_cfg_bridge_mode_get': return 1
        if name == 'plr_gponrm_dft_mbs_me_id_get':
            oracle.uc.mem_write(args[1], struct.pack('<H', 0x8080)); return 0
        if name == 'plr_gponrm_uni_bp_add':
            data = bytes(oracle.uc.mem_read(args[1], 20))
            card = struct.unpack_from('<H', data, 4)[0]
            kind = struct.unpack_from('<I', data, 8)[0]
            uni = struct.unpack_from('<H', data, 12)[0]
            bridge = struct.unpack_from('<H', data, 16)[0]
            calls.append([kind, card, uni, bridge])
            return 0
        raise AssertionError(('unmodeled UNI enumeration', name))
    overrides = ['plr_gponrm_cfg_bridge_mode_get', 'plr_gponrm_dft_mbs_me_id_get', 'plr_gponrm_uni_bp_add']
    for prefix in ('plr_gponrm_cfg_card_', 'plr_gponrm_card_'):
        for kind in ('eth', 'veip'):
            overrides += [prefix + kind + '_num_get', prefix + kind + '_uni_num_get']
    oracle = ElfOracle(binary, hook, overrides)
    cases = []
    for configured, reported in (
            ({'eth': [4], 'veip': [1]}, {'eth': [2], 'veip': [1]}),
            ({'eth': [2], 'veip': [2]}, {'eth': [4, 2], 'veip': [1]}),
            ({'eth': [], 'veip': [1]}, {'eth': [4], 'veip': []})):
        calls.clear()
        assert oracle.call('plr_gponrm_cfg_uni_bp_autocfg', oracle.scratch + 0x2000) == 0
        expected = [[value, card, uni, 0x8080] for kind, value in (('eth', 0), ('veip', 2))
                    for card in range(min(len(configured[kind]), len(reported[kind])))
                    for uni in range(min(configured[kind][card], reported[kind][card]))]
        assert calls == expected, (calls, expected)
        cases.append(dict(configured=configured, reported=reported, attachments=list(calls)))
    return cases


def default_uni_vlan(binary):
    """Execute the initialized UNI mode and its automatic VLAN row selection."""
    rows = []
    def hook(name, args):
        if name == 'lstInit':
            oracle.uc.mem_write(args[0], bytes(24)); return 0
        if name == 'lstFirst': return 0
        if name == 'install':
            rows.append(list(struct.unpack('<5H', oracle.uc.mem_read(args[1] + 40, 10))))
            return 0
        raise AssertionError(('unmodeled default UNI VLAN boundary', name))
    oracle = ElfOracle(binary, hook)
    oracle.symbols['auto_vlan'] = 0xb69bc
    oracle.hooks[0xb4778] = 'install'
    address, record, context = (oracle.scratch + n for n in (0x2000, 0x3000, 0x4000))
    oracle.uc.mem_write(record, bytes(96))
    oracle.call('plr_gponrm_cfg_card_uni_init', record)
    assert oracle.uc.mem_read(record + 19, 1) == b'\x04'
    oracle.uc.mem_write(context, struct.pack('<Q', record) + bytes(96))
    assert oracle.call('auto_vlan', address, context) == 0
    assert rows == [[0xfffe, 0xfffe, 0xfffe, 0xfffe, 0]]
    return dict(mode=4, rows=rows, scope='Actual configured UNI initializer and automatic mode selector; empty custom-rule list and row-install boundary are modeled. Default flow VLAN filters are independent of this transparent UNI mode.')


def default_profiles(binary, fpga=False):
    """Run profile initializers and their configured-card conversion, with pool/list IO modeled."""
    profiles, lists, next_node = {}, {}, 0
    recipe = None
    def hook(name, a):
        nonlocal next_node, recipe
        uc = oracle.uc
        if name == 'plr_pon_profile_add':
            label = bytes(uc.mem_read(a[1], 32)).split(b'\0')[0].decode()
            ptr = oracle.scratch + 0x3000 + a[0] * 256
            uc.mem_write(ptr, bytes(256)); uc.mem_write(a[2], struct.pack('<Q', ptr))
            profiles[label] = ptr
            return 0
        if name == 'plr_gponrm_cfg_onu_add':
            recipe = bytes(uc.mem_read(a[1], 52)); return 0
        if name == 'plr_gpon_maxpon_get': return 1
        if name == 'plr_gpon_maxonu_get': return 63
        if name in ('plr_gponrm_mgmt_sem_take_with_timeout', 'plr_gponrm_mgmt_sem_give'): return 0
        if name == 'plr_gponrm_mem_pool_alloc':
            assert a[0] in (15,16)
            ptr = oracle.scratch + 0x8000 + next_node * 256; next_node += 1
            uc.mem_write(ptr, bytes(256)); return ptr
        if name == 'lstInit': lists[a[0]] = []; return 0
        if name == 'lstCount': return len(lists.get(a[0], []))
        if name == 'lstAdd': lists.setdefault(a[0], []).append(a[1]); return 0
        if name == 'lstNth':
            items = lists.get(a[0], [])
            return items[a[1]-1] if 0 < a[1] <= len(items) else 0
        raise AssertionError(('default profile boundary', name, a))
    overrides = ('plr_pon_profile_add', 'plr_gponrm_cfg_onu_add', 'plr_gpon_maxpon_get',
        'plr_gpon_maxonu_get', 'plr_gponrm_mgmt_sem_take_with_timeout', 'plr_gponrm_mgmt_sem_give',
        'plr_gponrm_mem_pool_alloc')
    oracle = ElfOracle(binary, hook, overrides)
    oracle.call('plr_pon_bw_profile_init'); oracle.call('plr_pon_onutype_profile_init')
    bw = list(struct.unpack('<4I', oracle.uc.mem_read(profiles['FTTR-100M'] + 52,16)))
    assert bw == [3,0,50,100]
    address, input_ptr, cfg, storage = (oracle.scratch+n for n in (0x2000,0x2100,0x4000,0x5000))
    oracle.uc.mem_write(input_ptr, struct.pack('<HHQ',1,1,profiles['FTTR-402']))
    assert oracle.call('plr_gponrm_onu_add_api',input_ptr) == 0
    assert struct.unpack_from('<5H',recipe) == (1,4,1,2,0)
    oracle.uc.mem_write(cfg,recipe); oracle.uc.mem_write(address,bytes(4))
    oracle.uc.mem_write(0x171138 if fpga else 0x176378,struct.pack('<Q',storage))
    assert oracle.call('plr_gponrm_cfg_card_mgmt_add',address,cfg)==0
    configured = {}
    for kind, expected in (('eth',4),('veip',1)):
        assert oracle.call('plr_gponrm_cfg_card_'+kind+'_num_get',address)==1
        assert oracle.call('plr_gponrm_cfg_card_'+kind+'_uni_num_get',address,0,cfg+128)==0
        count=struct.unpack('<H',oracle.uc.mem_read(cfg+128,2))[0]
        assert count==expected; configured[kind]=[count]
    return dict(bandwidth_profile=dict(name='FTTR-100M',type=bw[0],fixed=bw[1],assured=bw[2],maximum=bw[3]),
        onu_type='FTTR-402',configured=configured,configuration=recipe.hex(),
        scope='Actual initializers, ONU-type adapter, configured-card allocation and getters. Profile allocator, pools, list storage and semaphore boundaries modeled; these nominal rates are not physical DBA.')


def reported_cards(binary):
    """Run original card/UNI producers, resolution and getters in deliberately unordered inventories."""
    lists, next_node = {}, 0
    def link(head):
        items=lists.setdefault(head,[])
        for i,node in enumerate(items):
            oracle.uc.mem_write(node,struct.pack('<QQ',items[i+1] if i+1<len(items) else 0,items[i-1] if i else 0))
    def hook(name,a):
        nonlocal next_node
        if name=='plr_gpon_maxonu_get':return 63
        if name in ('memcpy','memmove'):
            oracle.uc.mem_write(a[0],bytes(oracle.uc.mem_read(a[1],a[2])));return a[0]
        if name=='memset':oracle.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
        if name=='plr_gponrm_mem_pool_alloc':
            ptr=oracle.scratch+0x10000+next_node*256;next_node+=1
            oracle.uc.mem_write(ptr,bytes(256));return ptr
        if name=='plr_gponrm_mem_pool_free':return 0
        if name=='plr_log':return 0
        if name=='lstInit':lists[a[0]]=[];return 0
        if name=='lstCount':return len(lists.get(a[0],[]))
        if name=='lstFirst':return next(iter(lists.get(a[0],[])),0)
        if name=='lstNext':return struct.unpack('<Q',oracle.uc.mem_read(a[0],8))[0]
        if name=='lstNth':
            items=lists.get(a[0],[]);return items[a[1]-1] if 0<a[1]<=len(items) else 0
        if name=='lstAdd':lists.setdefault(a[0],[]).append(a[1]);link(a[0]);return 0
        if name=='lstInsert':
            items=lists.setdefault(a[0],[]);items.insert(items.index(a[1])+1 if a[1] else 0,a[2]);link(a[0]);return 0
        if name=='lstGet':
            items=lists.get(a[0],[]);node=items.pop(0) if items else 0;link(a[0]);return node
        raise AssertionError(('reported card boundary',name,a))
    oracle=ElfOracle(binary,hook,('plr_gpon_maxonu_get','plr_gponrm_mem_pool_alloc','plr_gponrm_mem_pool_free','plr_log'))
    address,record,storage=(oracle.scratch+n for n in (0x2000,0x3000,0x4000))
    oracle.uc.mem_write(0x176ae0,struct.pack('<Q',storage))
    cases=[]
    for cards,eth,veip in (([(9,48),(6,47),(8,48),(2,22)],[0x901,0x103,0x102,0x101,0x104],[0x902,0x801,0x901]),
                           ([(9,48),(2,22)],[0x701],[0x801]),([], [1],[0x101])):
        lists.clear();next_node=0;oracle.uc.mem_write(storage,bytes(755))
        for n,(instance,kind) in enumerate(cards):
            cls=5 if n%2==0 else 6
            wire=struct.pack('>HBBHHHHH',0x8123,0x2e,10,2,0,cls,instance,0x8000)
            oracle.uc.mem_write(record+256,wire+bytes([kind])+bytes(25)+b'\0\0\0\x28')
            assert oracle.call('plr_gponomci_me_uploadnext_cont_parse',cls,record+256,record,128)==0x80000000
            assert struct.unpack('<H',oracle.uc.mem_read(record,2))[0]==instance
            assert oracle.uc.mem_read(record+6,1)==bytes([kind])
            assert oracle.call('plr_gponrm_'+('card' if cls==5 else 'circuit')+'_write',address,record)==0
        for kind,ids in (('pptp_eth_uni',eth),('veip',veip)):
            for instance in ids:
                oracle.uc.mem_write(record,struct.pack('<H',instance)+bytes(40))
                assert oracle.call('plr_gponrm_'+kind+'_write',address,record)==0
        oracle.call('plr_gponrm_card_resolve',address)
        actual={}
        for kind in ('eth','veip'):
            groups=[]
            for card in range(oracle.call('plr_gponrm_card_'+kind+'_num_get',address)):
                assert oracle.call('plr_gponrm_card_'+kind+'_uni_num_get',address,card,record)==0
                count=struct.unpack('<H',oracle.uc.mem_read(record,2))[0]
                group=[]
                for uni in range(count):
                    ptr=oracle.call('plr_gponrm_card_'+kind+'_uni_get_by_card_uni_id',address,card,uni)
                    group.append(struct.unpack('<H',oracle.uc.mem_read(ptr+16,2))[0])
                groups.append(group)
            actual[kind]=groups
        eth_cards=sorted(i for i,k in cards if k in (22,23,24,34,47,49))
        veip_cards=sorted(i for i,k in cards if k==48)
        assert actual==dict(eth=([sorted(eth)]+[[]]*(len(eth_cards)-1) if eth_cards else []),
                           veip=[[i for i in sorted(veip) if i>>8==c&255] for c in veip_cards]),actual
        cases.append(dict(cards=cards,ethernet=eth,veip=veip,resolved=actual,
            selected=dict(eth=actual['eth'][0][:4] if actual['eth'] else [],veip=actual['veip'][0][:1] if actual['veip'] else [])))
    return cases


def default_queue(binary):
    """Execute API -> service GEM -> remote adapter and its Related Port composition."""
    from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_PC
    saved=[]
    def hook(name,a):
        u=oracle.uc
        if name in ('memcpy','memmove'):
            u.mem_write(a[0],bytes(u.mem_read(a[1],a[2])));return a[0]
        if name=='memset':u.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
        if name=='strcpy':
            value=bytes(u.mem_read(a[1],32)).split(b'\0')[0]+b'\0';u.mem_write(a[0],value);return a[0]
        if name=='validate':u.mem_write(a[2],struct.pack('<Q',oracle.scratch+0x2000));return 0
        if name=='plr_ponsrv_port_id_get':u.mem_write(a[3],struct.pack('<H',257));return 0
        if name=='plr_ifmng_gemport_add_notify':saved.append(bytes(u.mem_read(a[0],1074)));return 0
        if name=='plr_gponrm_cfg_gem_uc_op':saved.append(bytes(u.mem_read(a[1],42)));return 0
        if name=='plr_gponrm_queue_us_me_id_get_by_related_port_id':saved.append(a[1]);return 0
        if name in ('plr_ponsrv_cfgonu_sem_take_with_timeout','plr_ponsrv_cfgonu_sem_give',
                    'plr_ponsrv_onu_is_authed','plr_ponsrv_gemport_data_save',
                    'plr_gponrm_mgmt_sem_take_with_timeout','plr_gponrm_mgmt_sem_give'):return 0
        if name=='plr_gpon_maxpon_get':return 1
        if name=='plr_gpon_maxonu_get':return 63
        raise AssertionError(('default queue boundary',name,a))
    oracle=ElfOracle(binary,hook,('plr_ponsrv_port_id_get','plr_ifmng_gemport_add_notify',
        'plr_gponrm_cfg_gem_uc_op','plr_gponrm_queue_us_me_id_get_by_related_port_id',
        'plr_ponsrv_cfgonu_sem_take_with_timeout','plr_ponsrv_cfgonu_sem_give',
        'plr_ponsrv_onu_is_authed','plr_ponsrv_gemport_data_save',
        'plr_gponrm_mgmt_sem_take_with_timeout','plr_gponrm_mgmt_sem_give',
        'plr_gpon_maxpon_get','plr_gpon_maxonu_get'))
    oracle.hooks[0x8575c]='validate'
    oracle.uc.mem_write(oracle.scratch+0x2010,struct.pack('<I',1))
    assert oracle.call('plr_api_gem_add',1|(1<<32),1|(1<<32))==0
    ptr=oracle.scratch+0x3000
    oracle.uc.mem_write(ptr,saved[0]);assert oracle.call('plr_gponrm_gem_op_api',ptr)==0
    assert struct.unpack_from('<2H',saved[1],6)==(0,0)
    # Execute the real flow's Related Port composition with the just-produced priority.
    context=oracle.scratch+0x4000;record=context+256;stack=oracle.scratch+0x1ff000
    oracle.uc.mem_write(record+20,saved[1][6:8]);oracle.uc.mem_write(context+40,struct.pack('<Q',record))
    oracle.uc.reg_write(UC_ARM64_REG_SP,stack)
    oracle.uc.mem_write(stack+24,struct.pack('<Q',context));oracle.uc.mem_write(stack+40,struct.pack('<Q',ptr))
    oracle.uc.mem_write(stack+62,bytes(2));oracle.uc.mem_write(stack+98,struct.pack('<H',0x8001))
    oracle.uc.emu_start(0xaa164,0xaa1b8,count=1000)
    assert oracle.uc.reg_read(UC_ARM64_REG_PC)==0xaa1b8 and saved[-1]==0x80010000
    return dict(api_queue=1,remote_priority=0,related_port=saved[-1],
        scope='Actual GEM API/service/remote conversion and flow Related Port composition; validation, allocation, notification delivery and IO modeled.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('caller', 'relay', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--fpga-caller', type=Path)
    p.add_argument('--fpga-relay', type=Path)
    args = p.parse_args()
    assert bool(args.fpga_caller) == bool(args.fpga_relay)
    from trace_stock_fttr_backend import startup
    supervisor = startup(args.caller, 2, 9, slots=8)
    stock = DefaultService(args.caller)
    cases = []
    for onu in (1, 6, 63):
        for slots in (2, 3, 8):
            case = stock.run(onu, slots)
            assert case['result'] == 0
            actual = [(e['operation'], bytes.fromhex(e['payload'])) for e in case['events']]
            assert actual == expected(onu, slots), case
            cases.append(case)
    failures = []
    for slots in (2, 8):
        count = len(expected(6, slots))
        for fail in range(1, count + 1):
            case = stock.run(6, slots, fail)
            assert case['result'] == 17  # Original preserves the initiating error.
            assert len(case['events']) == fail + 3, case
            assert case['events'][fail - 1]['result'] == 17
            assert [e['operation'] for e in case['events'][fail:]] == [
                'plr_api_cfgonu_get', 'plr_api_onu_del', 'plr_api_onu_add']
            failures.append(case)
    translations = [relay_translation(args.relay, onu, slot, 100 * slot)
                    for onu in (1, 6, 63) for slot in (1, 7)]
    fpga = None
    if args.fpga_caller:
        factory = DefaultService(args.fpga_caller, True)
        fpga_cases = []
        for onu in (1, 6, 64):
            for fail in range(5):
                case = factory.run(onu, 2, fail)
                want = [(name.replace('plr_api_remote_onu_mcast_enable',
                         'plr_api_remote_onu_uni_mcast_enable'), payload)
                        for name, payload in expected(onu, 2)]
                if fail: want = want[:fail]
                assert [(e['operation'], bytes.fromhex(e['payload'])) for e in case['events']] == want
                assert case['result'] == (17 if fail else 0)
                fpga_cases.append(case)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        from test_fttr_fpga_dba import relay_bandwidth
        fpga = dict(cases=fpga_cases, profiles=default_profiles(args.fpga_relay, True),
                    shared_unis=shared_uni_selection(args.fpga_relay),
                    bandwidth_wrapper=relay_bandwidth(args.fpga_relay),
                    caller_sha256=hashlib.sha256(args.fpga_caller.read_bytes()).hexdigest(),
                    relay_sha256=hashlib.sha256(args.fpga_relay.read_bytes()).hexdigest(),
                    scope='Actual FPGA1 one-service producer, first-error stop, profile/card conversion and shared UNI enumeration. Bandwidth wrappers perform no hardware IO; no default grant value or native default-policy support is established.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(cases=cases, failures=failures, translations=translations, bridge_selection=bridge_selection(args.relay),
        default_profiles=default_profiles(args.relay), default_queue=default_queue(args.relay),
        supervisor_dba=supervisor['registration_dba'], reported_cards=reported_cards(args.relay),
        shared_uni_selection=shared_uni_selection(args.relay), default_uni_vlan=default_uni_vlan(args.relay),
        fpga1=fpga, caller_sha256=hashlib.sha256(args.caller.read_bytes()).hexdigest(),
        relay_sha256=hashlib.sha256(args.relay.read_bytes()).hexdigest(),
        findings=['Default two-slot ONU creates one named T-CONT/GEM/flow and enables multicast.',
                  'Larger slot count adds data slots2..7 and VLAN filters100*slot on every data flow.',
                  'Flow adapter updates GEM or VLAN fields without a UNI-binding mask.',
                  'With the first default flow, the original selector chooses the shared bridge; it does not select Ethernet UNI ordinal0.',
                  'Original failed provisioning deletes/re-adds the ONU, preserves the initiating error and ignores cleanup results.'],
        scope='Actual original producer and flow API adaptation; allocator/remote OMCI, semaphore, configuration owner and reset IO are modeled. No complete service/topology/traffic or hardware acceptance.'), indent=2) + '\n')
    print(f'PASS: {len(cases)} original default/multi-slot recipes, {len(failures)} failure paths, {len(translations)*2} flow adapter calls, 4 bridge selections, 3 shared-UNI enumerations and actual default transparent VLAN initialization; profiles/card resolution/default queue and eight-slot supervisor DBA also passed')
    if fpga:
        print('PASS: 15 FPGA1 one-service/first-error cases, actual profile/card conversion, shared-UNI selection and72 no-op bandwidth wrapper calls')


if __name__ == '__main__': main()
