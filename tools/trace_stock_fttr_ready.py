#!/usr/bin/env python3
"""Execute the retained master's readiness predicate with explicit WAN/CWMP fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from arm64_elf_oracle import ElfOracle


class ReadyOriginal:
    def __init__(self, mib):
        assert hashlib.sha256(Path(mib).read_bytes()).hexdigest() == '5e14985931f51977c0b0e190bfa1c16122b8614b596f58f7ad121a83beb37ec2'
        self.oracle = ElfOracle(mib, self.handler, ('mib_chain_total', 'mib_chain_get', 'mib_get_s',
                                                  'ifGetName', 'getInFlags', 'getInAddr'))
        self.rows = []
        self.status = 0
        self.result = 1
        self.calls = []
        self.current = None

    def handler(self, name, args):
        u = self.oracle.uc
        self.calls.append(name)
        if name == 'mib_chain_total':
            assert args[0] == 3004
            return len(self.rows)
        if name == 'mib_chain_get':
            assert args[0] == 3004
            self.current = self.rows[args[1]]
            row = bytearray(11637)
            struct.pack_into('<I', row, 0, args[1] + 1)
            struct.pack_into('<I', row, 8502, self.current.get('applicationtype', 1))
            u.mem_write(args[2], bytes(row))
            return self.current.get('row_ok', 1)
        if name == 'ifGetName':
            assert args[2] == 32
            u.mem_write(args[1], b'synthetic-wan\0')
            return args[1] if self.current.get('name_ok', 1) else 0
        if name == 'getInFlags':
            u.mem_write(args[1], struct.pack('<I', self.current.get('flags', 1)))
            return self.current.get('flags_ok', 1)
        if name == 'getInAddr':
            assert args[1] == 0
            u.mem_write(args[2], bytes(8))
            return self.current.get('ipv4_ok', 1)
        if name == 'mib_get_s':
            assert args[0] in (4111, 4115) and args[2] == 4
            u.mem_write(args[1], struct.pack('<I', self.status if args[0] == 4111 else self.result))
            return 1
        raise AssertionError(name)

    def ready(self, rows, status=0, result=1):
        self.rows, self.status, self.result, self.calls = rows, status, result, []
        return self.oracle.call('isMasterReady')


def definitions(configd):
    with Path(configd).open('rb') as stream:
        elf = ELFFile(stream)
        symbols = {s.name: s for s in elf.get_section_by_name('.dynsym').iter_symbols()}
        def read(address, size):
            for segment in elf.iter_segments():
                if segment['p_type'] == 'PT_LOAD' and segment['p_vaddr'] <= address and address + size <= segment['p_vaddr'] + segment['p_filesz']:
                    start = address - segment['p_vaddr']
                    return segment.data()[start:start + size]
            raise AssertionError(address)
        table = symbols['mib_table']
        data = read(table['st_value'], table['st_size'])
        names = {}
        for offset in range(0, len(data), 80):
            row = data[offset:offset + 80]
            ident = struct.unpack_from('<I', row)[0]
            if ident in (4111, 4115):
                names[str(ident)] = row[8:56].split(b'\0')[0].decode()
        assert names == {'4111': 'CWMP_USERINFO_STATUS', '4115': 'CWMP_USERINFO_RESULT'}
        chain = read(symbols['mib_chain_record_table']['st_value'], 80)
        assert struct.unpack_from('<I', chain)[0] == 3004 and chain[8:40].split(b'\0')[0] == b'ATM_VC_TBL'
        assert struct.unpack_from('<I', chain, 40)[0] == 11637
        pointer = struct.unpack_from('<Q', chain, 72)[0]
        for offset in range(0, 56000, 56):
            row = read(pointer + offset, 56)
            if row[:36].split(b'\0')[0] == b'applicationtype':
                assert struct.unpack_from('<III', row, 36) == (2, 8502, 4)
                names['3004+8502'] = 'ATM_VC_TBL.applicationtype'
                break
        assert '3004+8502' in names
        return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mib', type=Path, required=True)
    parser.add_argument('--configd', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stock = ReadyOriginal(args.mib)
    cases = []
    inputs = [('no-wan', [], 0), ('ready', [{}], 1), ('skip-failed-row', [{'row_ok': 0}, {}], 1),
              ('failed-row', [{'row_ok': 0}], 0), ('other-service', [{'applicationtype': 2}], 0),
              ('combined-service', [{'applicationtype': 3}], 1), ('no-name', [{'name_ok': 0}], 0),
              ('flags-error', [{'flags_ok': 0}], 0), ('down', [{'flags': 0}], 0),
              ('running-only', [{'flags': 64}], 0), ('up-no-running', [{'flags': 1}], 1),
              ('no-ipv4', [{'ipv4_ok': 0}], 0), ('later-wan', [{'ipv4_ok': 0}, {}], 1)]
    for name, rows, expected in inputs:
        assert stock.ready(rows) == expected, name
        cases.append({'case': name, 'ready': expected, 'calls': stock.calls})
    for status in (0, 1, 2, 0xffffffff):
        for result in (0, 1, 2, 0xffffffff):
            expected = int(status == 0 and result == 1)
            assert stock.ready([{}], status, result) == expected
            cases.append({'status': status, 'result': result, 'ready': expected})
    report = {'mib_sha256': hashlib.sha256(args.mib.read_bytes()).hexdigest(),
              'configd_sha256': hashlib.sha256(args.configd.read_bytes()).hexdigest(),
              'definitions': definitions(args.configd), 'cases': cases,
              'findings': ['WAN applicationtype bit0, successful interface-name and flag lookup, IFF_UP and successful IPv4 address lookup are required',
                           'requires CWMP_USERINFO_STATUS == 0 and CWMP_USERINFO_RESULT == 1',
                           'does not check MiniOLT optics, carrier/IFF_RUNNING, nonzero IPv4 address, internet reachability or slave acceptance'],
              'scope': 'Actual isMasterReady instructions and original configd definitions. WAN/MIB IO are fixtures, not measured network or CWMP readiness.'}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'cases': len(cases), 'definitions': report['definitions'], 'findings': report['findings']}, indent=2))


if __name__ == '__main__':
    main()
