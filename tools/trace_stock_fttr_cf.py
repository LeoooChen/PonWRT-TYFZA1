#!/usr/bin/env python3
"""Execute the original FC producer and L2 CF table writers for FTTR ingress."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE


class FactoryRules:
    def __init__(self, binary):
        self.rules, self.deleted = [], []
        self.oracle = ModuleOracle(binary, ['rtk_8277c_cls_init'], self.boundary)

    def boundary(self, name, a):
        u = self.oracle.uc
        if name == 'aal_l2_cf_rule_delete':
            self.deleted.append(a[1]); return 0
        if name == 'aal_l2_cf_rule_add':
            self.rules.append((a[1], bytes(u.mem_read(a[2], 240)))); return 0
        if name == 'memset':
            u.mem_write(a[0], bytes([a[1] & 255])*a[2]); return a[0]
        if name == 'sprintf':
            u.mem_write(a[0], b'traced\0'); return 6
        if name == 'printk':
            return 0
        raise AssertionError(('unmodeled FC boundary', name))


class CfTables:
    def __init__(self, binary):
        functions = ['aal_l2_cf_rule_add', 'aal_l2_cf_key_set', 'aal_l2_cf_action_set',
            '__aal_l2_cf_key_read', '__aal_l2_cf_key_write',
            '__aal_l2_cf_action_read', '__aal_l2_cf_action_write']
        self.oracle = ModuleOracle(binary, functions, self.boundary)
        self.base = 0x6000000
        self.oracle.uc.mem_map(self.base, 0x10000)
        self.oracle.word(self.oracle.symbols['g_ne_reg_iobase'][0], self.base)
        self.tables, self.writes = {}, []
        self.oracle.uc.hook_add(UC_HOOK_MEM_WRITE, self.write, begin=self.base, end=self.base+0xffff)
        self.oracle.uc.hook_add(UC_HOOK_MEM_READ, self.read, begin=self.base, end=self.base+0xffff)

    def boundary(self, name, args):
        if name in ('ca_spin_lock', 'ca_spin_unlock'):
            return 0
        raise AssertionError(('unmodeled CF boundary', name))

    def write(self, u, kind, address, size, value, unused):
        reg = address-self.base
        self.writes.append([reg, value])
        if reg not in (0x1e04, 0x1e14):
            return
        index = value & (1023 if reg == 0x1e04 else 511)
        data = 0x1e10 if reg == 0x1e04 else 0x1e20
        if value & (1 << 30):
            self.tables[reg, index] = [struct.unpack('<I', u.mem_read(self.base+data-i*4, 4))[0] for i in range(3)]
        else:
            # Canonical empty TCAM row represents zero data/care, valid=0.
            empty = [0xffffffff, 0xffffffff, 0xfffeffff] if reg == 0x1e04 else [0, 0, 0]
            for i, word in enumerate(self.tables.get((reg, index), empty)):
                u.mem_write(self.base+data-i*4, struct.pack('<I', word))

    def read(self, u, kind, address, size, value, unused):
        if address-self.base in (0x1e04, 0x1e14):
            value = struct.unpack('<I', u.mem_read(address, 4))[0]
            u.mem_write(address, struct.pack('<I', value & ~(1 << 31)))

    def add(self, index, recipe):
        self.writes.clear()
        pointer = self.oracle.scratch+0x1000
        self.oracle.uc.mem_write(pointer, recipe)
        assert self.oracle.call('aal_l2_cf_rule_add', 0, index, pointer) == 0
        return {'index': index, 'recipe': recipe.hex(),
            'tcam_x': self.tables[0x1e04, index], 'tcam_y': self.tables[0x1e04, index+512],
            'action': self.tables[0x1e14, index], 'writes': list(self.writes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fc', type=Path, required=True)
    parser.add_argument('--ni', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    factory = FactoryRules(args.fc)
    assert factory.oracle.call('rtk_8277c_cls_init') == 0
    assert factory.deleted == list(range(16)) and [i for i, _ in factory.rules] == [0, 1]
    tables = CfTables(args.ni)
    original = [tables.add(index, recipe) for index, recipe in factory.rules]
    wan, lan = [struct.unpack('<60I', recipe) for _, recipe in factory.rules]
    assert wan[0] == wan[20] == lan[0] == lan[20] == 1
    assert (wan[18], wan[38], wan[50], wan[51]) == (7, 63, 3, 24)
    assert (lan[18], lan[38], lan[50], lan[51]) == (0, 0, 3, 25)
    # Narrow the original LAN rule to physical source6, preserving its exact
    # fields/actions. This records a candidate P6 rule, not a hardware result.
    p6 = bytearray(factory.rules[1][1])
    struct.pack_into('<I', p6, 72, 6)
    struct.pack_into('<I', p6, 152, 63)
    candidate = tables.add(2, bytes(p6))
    assert candidate['action'] == original[1]['action'] == [0, 3 << 5 | 25 << 8, 0]
    assert candidate['tcam_x'] == [~(6 << 6) & 0xffffffff, 0xffffffff, 0xffffffff]
    assert candidate['tcam_y'] == [(6 << 6) | (~(63 << 6) & 0xffffffff), 0xffffffff, 0xffffffff]
    key_publish = next(i for i, (reg, value) in enumerate(candidate['writes'])
        if reg == 0x1e04 and value == 0xc0000202)
    action_publish = next(i for i, (reg, value) in enumerate(candidate['writes'])
        if reg == 0x1e14 and value == 0xc0000002)
    assert key_publish < action_publish
    report = {'sha256': {args.fc.name: hashlib.sha256(args.fc.read_bytes()).hexdigest(),
        args.ni.name: hashlib.sha256(args.ni.read_bytes()).hexdigest()},
        'factory': original, 'p6_candidate': candidate,
        'finding': 'Original FC rules route source7 to24 and remaining sources to25; P6-specific matching uses source6/care63. Original add publishes the valid key before its action.',
        'scope': 'Original ARM64 FC producer and NI CF writers, with only formatting/locks and indirect table hardware modeled. No NI port, CF rule or FE flow is enabled on real hardware.'}
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({key: value for key, value in report.items() if key not in ('factory', 'p6_candidate')}, indent=2))


if __name__ == '__main__':
    main()
