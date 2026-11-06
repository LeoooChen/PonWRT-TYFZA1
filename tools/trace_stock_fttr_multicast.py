#!/usr/bin/env python3
"""Execute the default multicast API through original OMCI producers."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_elf_oracle import ElfOracle


class Multicast:
    def __init__(self, binary):
        self.events = []
        overrides = ('plr_log', 'plr_gponrm_card_veip_num_get', 'plr_gpon_maxpon_get',
            'plr_gpon_maxonu_get', 'plr_gponrm_mgmt_sem_take_with_timeout',
            'plr_gponrm_mgmt_sem_give', 'plr_gponrm_cfg_state_get',
            'plr_gponrm_cfg_card_eth_uni_get_by_card_uni_id',
            'plr_gponrm_cfg_card_veip_uni_get_by_card_uni_id',
            'plr_gponrm_mem_pool_alloc', 'plr_gponrm_mem_pool_free',
            'plr_gponrm_cfg_bridge_mode_get', 'plr_gponrm_cfg_card_uni_mul_ref_get',
            'plr_gponrm_cfg_card_uni_mul_ref_increase',
            'plr_gponrm_card_eth_uni_get_by_card_uni_id_',
            'plr_gponrm_card_veip_uni_get_by_card_uni_id_', 'plr_gponrm_onu_mgmt_get',
            'plr_gponrm_pon_protect_is_standby_port', 'plr_gponrm_onu_hw_id_get_by_lg_id',
            'plr_gponomci_sync_api', 'plr_gponrm_flow_mgmt_get',
            'plr_gponrm_dft_mbs_me_id_get', 'plr_gponrm_mbp_me_id_alloc',
            'plr_gponrm_mbs_port_alloc', 'plr_gponrm_mbs_port_free', 'plr_gponrm_mbp_me_id_free',
            'plr_gponrm_card_eth_uni_get_by_card_uni_id', 'plr_gponrm_card_veip_uni_get_by_card_uni_id',
            'plr_gponrm_card_eth_uni_mbs_me_id_get_by_card_uni_id',
            'plr_gponrm_card_veip_uni_mbs_me_id_get_by_card_uni_id', 'plr_gponomci_me_high_prio_tx',
            'plr_gponrm_gal_me_id_get', 'plr_gponrm_gem_mc_get')
        self.oracle = ElfOracle(binary, self.boundary, overrides)
        self.base = self.oracle.scratch + 0x2000
        self.oracle.symbols.update(stock_create=0x6535c, stock_set=0x64f8c, stock_delete=0x659c0,
                                   multicast_gem=0xe09d4)
        self.extended_flag = self.oracle.symbols['g_plrGponrmCtcDebug']

    def boundary(self, name, a):
        uc, p = self.oracle.uc, self.base
        if name in ('memset', 'memmove'):
            uc.mem_write(a[0], bytes([a[1] & 255]) * a[2] if name == 'memset' else bytes(uc.mem_read(a[1], a[2])))
            return a[0]
        if name in ('plr_log', 'plr_gponrm_mgmt_sem_take_with_timeout',
                    'plr_gponrm_mgmt_sem_give', 'plr_gponrm_pon_protect_is_standby_port'):
            return 0
        if name == 'plr_gpon_maxpon_get': return 1
        if name == 'plr_gpon_maxonu_get': return 63
        if name == 'plr_gponrm_card_veip_num_get': return self.veip_count
        if name == 'plr_gponrm_cfg_state_get': return 3
        if name == 'plr_gponrm_cfg_bridge_mode_get': return self.bridge_mode
        if name.startswith('plr_gponrm_cfg_card_') and name.endswith('_uni_get_by_card_uni_id'):
            self.events.append(dict(operation='select', kind='veip' if '_veip_' in name else 'ethernet',
                address=bytes(uc.mem_read(a[0], 4)).hex(), card=a[1], uni=a[2]))
            return p
        if name == 'plr_gponrm_mem_pool_alloc':
            assert a[0] == 22
            return p + 0x100
        if name == 'plr_gponrm_mem_pool_free':
            self.events.append(dict(operation='free-config', pool=a[0])); return 0
        if name == 'plr_gponrm_cfg_card_uni_mul_ref_get': return self.references
        if name == 'plr_gponrm_cfg_card_uni_mul_ref_increase':
            self.references += 1
            self.events.append(dict(operation='reference', count=self.references)); return 0
        if name in ('plr_gponrm_card_eth_uni_get_by_card_uni_id_', 'plr_gponrm_card_veip_uni_get_by_card_uni_id_'):
            uc.mem_write(a[3], struct.pack('<Q', p + 0x200)); return 0
        if name == 'plr_gponrm_onu_mgmt_get': return p + 0x400
        if name == 'plr_gponrm_gem_mc_get': return p + 0x300
        if name == 'plr_gponrm_gal_me_id_get':
            uc.mem_write(a[1], struct.pack('<H', 1)); return self.gal_result
        if name == 'plr_gponrm_flow_mgmt_get': return p + 0x800
        if name in ('plr_gponrm_card_eth_uni_get_by_card_uni_id', 'plr_gponrm_card_veip_uni_get_by_card_uni_id'):
            return p + 0x200
        if name == 'plr_gponrm_dft_mbs_me_id_get':
            uc.mem_write(a[1], struct.pack('<H', 0x8080)); return 0
        if name in ('plr_gponrm_card_eth_uni_mbs_me_id_get_by_card_uni_id',
                    'plr_gponrm_card_veip_uni_mbs_me_id_get_by_card_uni_id'):
            uc.mem_write(a[3], struct.pack('<H', 0x8081)); return 0
        if name == 'plr_gponrm_mbp_me_id_alloc':
            uc.mem_write(a[1], struct.pack('<H', 10)); return 0
        if name == 'plr_gponrm_mbs_port_alloc':
            uc.mem_write(a[2], bytes([10])); return 0
        if name in ('plr_gponrm_mbs_port_free', 'plr_gponrm_mbp_me_id_free'): return 0
        if name == 'plr_gponrm_onu_hw_id_get_by_lg_id':
            uc.mem_write(a[1], struct.pack('<I', self.onu - 1)); return 0
        if name == 'plr_gponomci_sync_api':
            ram = bytes(uc.mem_read(a[1], a[2]))
            action = struct.unpack_from('<I', ram, 4)[0]
            self.requests += 1
            status = 6 if self.requests == self.fail_at or (self.fail_cleanup and action == 4) else 0
            self.events.append(dict(operation='omci', class_id=a[0],
                action=action, recipe=ram.hex(), result=status))
            uc.mem_write(a[3], bytes(a[4])); uc.mem_write(a[3] + 10, struct.pack('<I', status))
            return 0
        if name == 'plr_gponomci_me_high_prio_tx':
            self.frame = bytes(uc.mem_read(a[2], 44)); return 0x80000000
        raise AssertionError(('unmodeled multicast boundary', name))

    def encode(self):
        p, oracle = self.base + 0x1200, self.oracle
        for event in self.events:
            if event['operation'] != 'omci': continue
            data = bytes.fromhex(event['recipe'])
            oracle.uc.mem_write(p, data)
            name = {1:'stock_set', 3:'stock_create', 4:'stock_delete'}[event['action']]
            args = [0x8123, event['class_id'], p]
            args += [struct.unpack_from('<H', data, 10)[0]] if event['action'] == 4 else [p + 10, len(data) - 10]
            assert oracle.call(name, *args) == 0x80000000
            event['wire'] = self.frame.hex()

    def run(self, veip_count=1, extended=False, bridge_mode=1, references=0, fail_at=0, fail_cleanup=False):
        self.events.clear()
        self.onu, self.veip_count, self.bridge_mode, self.references = 6, veip_count, bridge_mode, references
        self.requests, self.fail_at, self.fail_cleanup = 0, fail_at, fail_cleanup
        uc, p = self.oracle.uc, self.base
        uc.mem_write(p, bytes(0x1000))
        # Use the actual pool initializer, including the original IGMP defaults.
        self.oracle.call('plr_gponrm_cfg_card_uni_mul_init', p + 0x100)
        # Only the selected reported UNI's bridge port and ownership slots are modeled.
        uc.mem_write(p + 0x200 + (52 if veip_count else 55), struct.pack('<H', 9 if veip_count else 1))
        uc.mem_write(p + 0x200 + (57 if veip_count else 60), b'\xff' * 4)
        uc.mem_write(p + 0x200 + (61 if veip_count else 64), b'\xff\xff')
        uc.mem_write(p + 0x800 + 24, b'\xff\xff')
        uc.mem_write(self.extended_flag, bytes([extended]))
        value = struct.pack('<H2xIH2x', 1, 2, 1)
        first, second = struct.unpack('<QQ', value.ljust(16, b'\0'))
        result = self.oracle.call('plr_api_remote_onu_mcast_enable', self.onu, first, second, 1)
        self.encode()
        return dict(veip_cards=veip_count, extended=extended, bridge_mode=bridge_mode,
                    initial_references=references, fail_at=fail_at, fail_cleanup=fail_cleanup,
                    result=result, events=list(self.events))

    def gem(self, fail_at=0, fail_cleanup=False, default=False, gal_result=0):
        self.events.clear()
        self.onu, self.requests, self.fail_at, self.fail_cleanup = 6, 0, fail_at, fail_cleanup
        self.gal_result = gal_result
        uc, p = self.oracle.uc, self.base
        uc.mem_write(p, bytes(0x1000))
        uc.mem_write(p, struct.pack('<HH', 0, self.onu - 1))
        uc.mem_write(p + 0x100 + 46, struct.pack('<H', 1))
        uc.mem_write(p + 0x200 + 16, struct.pack('<Q', p + 0x300))
        uc.mem_write(p + 0x300, b'\xff' * 4)
        result = self.oracle.call('plr_gponrm_cfg_gem_dft' if default else 'multicast_gem', p, p + 0x100, p + 0x200)
        self.encode()
        return dict(fail_at=fail_at, fail_cleanup=fail_cleanup, default=default, gal_result=gal_result,
                    result=result, events=list(self.events))


def table_snapshot(binary):
    """Execute the ONU's class281 table snapshot; model only MIB/list storage."""
    allocations = []
    def boundary(name, a):
        uc = oracle.uc
        if name in ('printf', 'putchar', 'free'): return 0
        if name in ('GOS_Htons', 'GOS_Htonl'):
            width = 2 if name.endswith('s') else 4
            return int.from_bytes((a[0] & ((1 << (width * 8)) - 1)).to_bytes(width, 'little'), 'big')
        if name == 'mib_FindEntry':
            uc.mem_write(a[2], struct.pack('<Q', entry)); return 1
        if name == 'MIB_Get':
            if a[2] == 18:
                uc.mem_write(a[1] + 2, struct.pack('<H', 4095)); return 0
            assert a[2] == 40
            return 1  # Missing upstream queue does not apply to downstream-only GEM.
        if name == 'MIB_IsInAttrSet': return a[1] == 10
        if name == 'malloc':
            allocations.append(a[0])
            uc.mem_write(buffer, b'\xa5' * a[0]); return buffer
        raise AssertionError(('unmodeled multicast table boundary', name))
    oracle = ElfOracle(binary, boundary)
    p = oracle.scratch + 0x2000
    entry, buffer, snapshots, info = (p + n for n in (0x100, 0x1000, 0x2000, 0x3000))
    # gOmciMulGetData and gInfo are supplied by omci_app, absent in this DSO.
    oracle.uc.mem_write(0x16f60, struct.pack('<Q', snapshots))
    oracle.uc.mem_write(0x16fe0, struct.pack('<Q', info))
    level = struct.unpack('<Q', oracle.uc.mem_read(0x16fd8, 8))[0]
    oracle.uc.mem_write(level, bytes(4))
    cases = []
    for count in (0, 1, 2):
        allocations.clear()
        oracle.uc.mem_write(p, bytes(0x4000))
        oracle.uc.mem_write(p, struct.pack('<HH', 4095, 4095))
        oracle.uc.mem_write(entry + 50, struct.pack('<I', count))
        oracle.uc.mem_write(entry + 58, struct.pack('<Q', p + 0x400 if count else 0))
        expected = b''
        for n in range(count):
            row = struct.pack('<HHII', 4095, n, 0xe0000100 + n * 256, 0xe00001ff + n * 256)
            next_row = p + 0x400 + (n + 1) * 32 if n + 1 < count else 0
            oracle.uc.mem_write(p + 0x400 + n * 32, row + struct.pack('<QQ', next_row, 0))
            expected += struct.pack('>HHII', *struct.unpack('<HHII', row))
        assert oracle.call('MultiGemIwTpDrvCfg', 1, p, 1, 1 << 9, 0) == 0
        attr, chunks, pointer, length, offset = struct.unpack('<IH2xQII', oracle.uc.mem_read(snapshots + 328, 24))
        assert attr == 10 and length == count * 24 and chunks == (length + 28) // 29 and not offset
        assert allocations == ([count * 24] if count else [])
        if count:
            actual = bytes(oracle.uc.mem_read(pointer, length))
            assert actual[:count * 12] == expected and actual[count * 12:] == b'\xa5' * (count * 12)
        cases.append(dict(rows=count, declared_bytes=length, serialized_row_bytes=count * 12,
                          unwritten_bytes=count * 12))
    return dict(cases=cases, sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        finding='Original class281 IPv4 Get snapshot allocates/reports24 bytes per row but writes12; remaining bytes retain modeled allocation poison. Do not treat padding as valid entries or fabricate a successful native readback.')


def table_mutations(binary):
    """Execute class281 Set/Delete with its actual list and callback behavior."""
    events=[]
    allocations=[]
    callback_error=0
    def boundary(name,a):
        uc=oracle.uc
        if name in ('printf','putchar'):return 0
        if name in ('GOS_Htons','GOS_Htonl'):
            width=2 if name.endswith('s') else 4
            return int.from_bytes((a[0]&((1<<(width*8))-1)).to_bytes(width,'little'),'big')
        if name=='mib_FindEntry':uc.mem_write(a[2],struct.pack('<Q',entry));return 1
        if name=='MIB_Get':
            if a[2]==18:
                uc.mem_write(a[1]+2,struct.pack('<H',4095));uc.mem_write(a[1]+6,b'\2');return 0
            assert a[2]==40
            return 1
        if name=='MIB_IsInAttrSet':return a[1]==10
        if name=='memcpy':uc.mem_write(a[0],bytes(uc.mem_read(a[1],a[2])));return a[0]
        if name=='malloc':
            assert a[0]==28
            pointer=p+0x8000+len(allocations)*32
            allocations.append(pointer);uc.mem_write(pointer,bytes(32));return pointer
        if name=='free':events.append(dict(operation='free'));return 0
        if name=='acl':
            row=struct.unpack('<HHII',uc.mem_read(a[2],12))
            events.append(dict(operation='acl',action=a[0],family=a[1],row=list(row),result=callback_error))
            return callback_error
        if name=='filter':events.append(dict(operation='filter',value=a[0]));return 0
        if name=='omci_wrapper_cfgGemFlow':
            events.append(dict(operation='flow',value=bytes(uc.mem_read(a[0],76)).hex()));return 0
        raise AssertionError(('unmodeled multicast mutation',name))
    oracle=ElfOracle(binary,boundary)
    p=oracle.scratch+0x2000
    entry,info,ops=p+0x100,p+0x1000,p+0x2000
    oracle.uc.mem_write(0x16fe0,struct.pack('<Q',info))
    level=struct.unpack('<Q',oracle.uc.mem_read(0x16fd8,8))[0]
    oracle.uc.mem_write(level,bytes(4))
    oracle.hooks[p+0x6000]='acl';oracle.hooks[p+0x6010]='filter'
    rows=[]
    for case,sequence,error in (
        ('add-delete',('add','delete'),0),
        ('delete-absent',('delete',),0),
        ('lost-delete-retry',('add','delete','delete'),0),
        ('acl-add-failure',('add',),7),
        ('acl-delete-failure',('add','delete'),7)):
        oracle.uc.mem_write(p,bytes(0x9000));allocations.clear()
        oracle.uc.mem_write(p,struct.pack('<HH',4095,4095))
        oracle.uc.mem_write(info+376,struct.pack('<Q',ops))
        oracle.uc.mem_write(ops+192,struct.pack('<QQ',p+0x6000,p+0x6010))
        steps=[]
        for operation in sequence:
            events.clear()
            callback_error=error if case!='acl-delete-failure' or operation=='delete' else 0
            oracle.uc.mem_write(p+14,bytes.fromhex('0fff0000')+(bytes.fromhex('e0000100efffffff') if operation=='add' else bytes(8)))
            result=oracle.call('MultiGemIwTpDrvCfg',1,p,0,1<<9,0)
            count=struct.unpack('<I',oracle.uc.mem_read(entry+50,4))[0]
            head=struct.unpack('<Q',oracle.uc.mem_read(entry+58,8))[0]
            row=list(struct.unpack('<HHII',oracle.uc.mem_read(head,12))) if head else None
            steps.append(dict(operation=operation,result=result,rows=count,row=row,events=list(events)))
        rows.append(dict(case=case,steps=steps))
    assert [s['rows'] for s in rows[0]['steps']]==[1,0]
    assert rows[1]['steps'][0]['row']==[4095,0,0,0]
    assert [s['rows'] for s in rows[2]['steps']]==[1,0,1]
    assert rows[3]['steps'][0]['result']==0 and rows[3]['steps'][0]['rows']==1
    assert rows[4]['steps'][-1]['result']==0 and rows[4]['steps'][-1]['rows']==0
    return dict(cases=rows,sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        finding='An absent delete key inserts a zero-address row; lost-delete retry is not idempotent. ACL callback errors are logged but MIB list mutation and success continue, so OMCI readback alone does not prove hardware ACL installation or removal.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--relay', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--table-mib', type=Path, help='execute original subordinate class281 IPv4 table snapshots')
    args = parser.parse_args()
    stock = Multicast(args.relay)
    cases, failures = [], []
    for veip in (0, 1, 3):
        for extended in (False, True):
            for bridge, refs in ((1, 0), (1, 1), (0, 0)):
                case = stock.run(veip_count=veip, extended=extended, bridge_mode=bridge, references=refs)
                assert case['result'] == 0
                selection = case['events'][0]
                assert selection == dict(operation='select', kind='veip' if veip else 'ethernet',
                    address='00000500', card=0, uni=0)
                requests = [e for e in case['events'] if e['operation'] == 'omci']
                profile = 65531 if extended else 309
                expected = [(profile, 3), (310, 3), (311, 3), (profile, 1)] if extended else [
                    (profile, 3), (profile, 1), (310, 3), (311, 3)]
                if bridge == 0 or not refs: expected += [(47, 3), (84, 3)]
                assert [(e['class_id'], e['action']) for e in requests] == expected
                for event in requests:
                    wire = bytes.fromhex(event['wire'])
                    assert wire[:4] == bytes.fromhex('8123') + bytes([{1:0x48, 3:0x44}[event['action']], 10])
                    assert struct.unpack_from('>H', wire, 4)[0] == event['class_id']
                    if event['class_id'] == 47:
                        assert wire[8:14] == struct.pack('>HBBH', 0x8080 if bridge else 0x8081, 10, 6, 4095)
                assert case['events'][-1] == dict(operation='reference', count=refs + 1)
                cases.append(case)
    for extended in (False, True):
        for at in range(1, 7):
            for cleanup in (False, True):
                case = stock.run(extended=extended, fail_at=at, fail_cleanup=cleanup)
                # The standard profile's downstream-tag Set result is overwritten
                # by the subscriber Create result. Preserve that evidence, not the bug.
                ignored = not extended and at == 2
                assert case['result'] == (0 if ignored else 55)
                if not ignored:
                    assert not any(e['operation'] == 'reference' for e in case['events'])
                    assert case['events'][-1] == dict(operation='free-config', pool=22)
                if extended and at in (2, 3, 4):
                    # Failed extended setup incorrectly tears down class309.
                    assert any(e.get('class_id') == 309 and e.get('action') == 4 for e in case['events'])
                failures.append(case)
    gems = [stock.gem()] + [stock.gem(at, cleanup) for at in range(1, 4) for cleanup in (False, True)]
    assert [(e['class_id'], e['action']) for e in gems[0]['events']] == [(268,3),(281,3),(281,1)]
    assert not gems[0]['result'] and all(g['result'] == 55 for g in gems[1:])
    defaults = [stock.gem(default=True, fail_at=at) for at in range(4)]
    assert defaults[0]['events'] == gems[0]['events']
    # c9d9c returns only error13; other real create/set failures become success.
    assert all(g['result'] == 0 for g in defaults)
    for error in (13, 31):
        case = stock.gem(default=True, gal_result=error)
        assert case['result'] == (13 if error == 13 else 0) and not case['events']
        defaults.append(case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(cases=cases, failures=failures, multicast_gem=gems, default_gem=defaults,
        table_snapshot=table_snapshot(args.table_mib) if args.table_mib else None,
        table_mutations=table_mutations(args.table_mib) if args.table_mib else None,
        relay_sha256=hashlib.sha256(args.relay.read_bytes()).hexdigest(),
        scope='Original API, multicast/flow/GEM/default-GEM producers and wire encoders; configuration/MIB storage, reported UNI/bridge IDs, allocators and OMCI replies modeled. Default-GEM execution does not exercise the full ONU startup ordering. No hardware or multicast traffic acceptance.'), indent=2) + '\n')
    print(f'PASS: {len(cases)} default multicast recipes, {len(failures)} operation/cleanup failures, {len(gems)} multicast GEM recipes/failures and {len(defaults)} default-GEM calls')


if __name__ == '__main__': main()
