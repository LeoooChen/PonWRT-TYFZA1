#!/usr/bin/env python3
"""Execute the original default ONU service producer and selected relay API adapters."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from arm64_elf_oracle import ElfOracle


class DefaultService:
    def __init__(self, binary):
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
        # fpga_api_onu_init's operation table selects ctc_ponrelay_onu_init.
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
        elif name == 'plr_api_remote_onu_mcast_enable':
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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('caller', 'relay', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(cases=cases, failures=failures, translations=translations, bridge_selection=bridge_selection(args.relay),
        caller_sha256=hashlib.sha256(args.caller.read_bytes()).hexdigest(),
        relay_sha256=hashlib.sha256(args.relay.read_bytes()).hexdigest(),
        findings=['Default two-slot ONU creates one named T-CONT/GEM/flow and enables multicast.',
                  'Larger slot count adds data slots2..7 and VLAN filters100*slot on every data flow.',
                  'Flow adapter updates GEM or VLAN fields without a UNI-binding mask.',
                  'With the first default flow, the original selector chooses the shared bridge; it does not select Ethernet UNI ordinal0.',
                  'Original failed provisioning deletes/re-adds the ONU, preserves the initiating error and ignores cleanup results.'],
        scope='Actual original producer and flow API adaptation; allocator/remote OMCI, semaphore, configuration owner and reset IO are modeled. No complete service/topology/traffic or hardware acceptance.'), indent=2) + '\n')
    print(f'PASS: {len(cases)} original default/multi-slot recipes, {len(failures)} failure paths, {len(translations)*2} flow adapter calls')


if __name__ == '__main__': main()
