#!/usr/bin/env python3
"""Inventory the frozen factory module, including registered callback tables."""
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
with module.open('rb') as stream:
    elf = ELFFile(stream)
    symbols = list(elf.get_section_by_name('.symtab').iter_symbols())
    functions = [s for s in symbols if s['st_info']['type'] == 'STT_FUNC'
                 and isinstance(s['st_shndx'], int)]
    starts, by_address = defaultdict(list), defaultdict(list)
    for s in functions:
        starts[s['st_shndx']].append(s['st_value'])
        by_address[s['st_shndx'], s['st_value']].append(s)
    starts = {k: sorted(set(v)) for k, v in starts.items()}

    def owner(section, offset):
        index = bisect_right(starts.get(section, []), offset) - 1
        if index < 0:
            return None
        candidates = by_address[section, starts[section][index]]
        return next((s for s in candidates
                     if s['st_value'] <= offset < s['st_value'] + s['st_size']), None)

    tables = [s for s in symbols if s['st_info']['type'] == 'STT_OBJECT'
              and isinstance(s['st_shndx'], int) and s['st_size']]
    objects, object_starts = defaultdict(list), defaultdict(list)
    for s in tables:
        objects[s['st_shndx'], s['st_value']].append(s)
        object_starts[s['st_shndx']].append(s['st_value'])
    object_starts = {k: sorted(set(v)) for k, v in object_starts.items()}
    table_rows, calls, relocations = defaultdict(list), set(), {}
    for section in elf.iter_sections():
        if section['sh_type'] != 'SHT_RELA':
            continue
        target = section['sh_info']
        for rel in section.iter_relocations():
            symbol = symbols[rel['r_info_sym']]
            kind, offset = rel['r_info_type'], rel['r_offset']
            relocations[target, offset] = (symbol, rel['r_addend'])
            resolved = by_address.get((symbol['st_shndx'], symbol['st_value'] + rel['r_addend']), [])
            names = [s.name for s in resolved] or ([symbol.name] if symbol.name else [])
            if kind in (282, 283):
                caller = owner(target, offset)
                if caller is not None:
                    for name in names:
                        calls.add((caller.name, name, offset, 'relocated-branch'))
            if kind == 257 and resolved:
                index = bisect_right(object_starts.get(target, []), offset) - 1
                candidates = objects[target, object_starts[target][index]] if index >= 0 else []
                for table in candidates:
                    if offset < table['st_value'] + table['st_size']:
                        for name in names:
                            table_rows[(table.name, table['st_shndx'], table['st_value'])].append(
                                dict(offset=hex(offset - table['st_value']), function=name,
                                     relocation_symbol=symbol.name, addend=rel['r_addend']))
    for section_id, offsets in starts.items():
        section = elf.get_section(section_id)
        data = section.data()
        for offset in range(0, len(data) - 3, 4):
            instruction = struct.unpack_from('<I', data, offset)[0]
            if instruction & 0xfc000000 not in (0x14000000, 0x94000000) or (section_id, offset) in relocations:
                continue
            immediate = instruction & 0x3ffffff
            target = offset + ((immediate ^ 0x2000000) - 0x2000000) * 4
            targets = by_address.get((section_id, target), [])
            caller = owner(section_id, offset)
            if caller is not None:
                for callee in targets:
                    if caller.name != callee.name:
                        calls.add((caller.name, callee.name, offset, 'decoded-direct-branch'))
    modinfo = elf.get_section_by_name('.modinfo').data().decode().split('\0')
    mib = next(s for s in symbols if s.name == 'mib_table')
    mib_data = elf.get_section(mib['st_shndx']).data()[mib['st_value']:mib['st_value'] + mib['st_size']]
    assert len(mib_data) % 64 == 0
    mib_rows = [dict(offset=hex(offset),
                     name=mib_data[offset:offset + 32].split(b'\0')[0].decode('ascii'),
                     raw_fields_u32=list(struct.unpack_from('<6I', mib_data, offset + 32)))
                for offset in range(0, len(mib_data), 64)]
    parameters = []
    for info in modinfo:
        if not info.startswith('parmtype='):
            continue
        name, kind = info[len('parmtype='):].split(':', 1)
        matches = [s for s in symbols if s.name == name and isinstance(s['st_shndx'], int)]
        row = dict(name=name, type=kind)
        if len(matches) == 1:
            s = matches[0]
            section = elf.get_section(s['st_shndx'])
            data = bytes(s['st_size']) if section['sh_type'] == 'SHT_NOBITS' else section.data()[s['st_value']:s['st_value'] + s['st_size']]
            row['initial_bytes_hex'] = data.hex()
            if kind in ('int', 'uint', 'byte') and len(data) in (1, 4):
                row['compiled_initial_value'] = int.from_bytes(data, 'little', signed=kind == 'int')
        parameters.append(row)
    output = dict(factory_module_sha256=digest,
        functions=[dict(name=s.name, section=elf.get_section(s['st_shndx']).name,
                        offset=hex(s['st_value']), bytes=s['st_size']) for s in functions],
        callback_tables=[dict(name=name, section=elf.get_section(section).name,
                              offset=hex(offset), entries=rows)
                         for (name, section, offset), rows in sorted(table_rows.items())],
        direct_calls=[dict(caller=a, callee=b, offset=hex(c), evidence=d)
                      for a, b, c, d in sorted(calls)],
        module_parameters=parameters, legacy_mib_fields=mib_rows,
        static_capability_objects=[dict(name=s.name, bytes=s['st_size'],
            raw_hex=elf.get_section(s['st_shndx']).data()[s['st_value']:s['st_value'] + s['st_size']].hex())
            for s in symbols if s.name in ('rtw_cipher_suites', 'rtw_akm_suites',
                                          'wowlan_stub', 'rtw_combinations')],
        modinfo=[s for s in modinfo if s],
        limitations=['Symbol and callback presence does not establish board enablement.',
                      'Compiled parameter initializers are not final flash/runtime settings.',
                      'Direct branches and relocated callback slots are retained; computed indirect targets are not resolved.',
                      'No inference of equivalent features from matching symbol names in native code.'])
print(json.dumps(output, indent=2))
