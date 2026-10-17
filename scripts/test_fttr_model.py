#!/usr/bin/env python3
"""Compare native MIB wire layouts with original descriptors and parser execution."""
import argparse
import ctypes
import errno
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_elf_oracle import ElfOracle


class Record(ctypes.Structure):
    _fields_ = [('class_id', ctypes.c_ushort), ('instance', ctypes.c_ushort),
                ('mask', ctypes.c_ushort), ('attributes', ctypes.c_ubyte * 26)]


class Entity(ctypes.Structure):
    _fields_ = [('next', ctypes.c_void_p), ('schema', ctypes.c_void_p),
                ('class_id', ctypes.c_ushort), ('instance', ctypes.c_ushort),
                ('reported', ctypes.c_ushort), ('present', ctypes.c_ushort)]


class Model(ctypes.Structure):
    _fields_ = [('entities', ctypes.c_void_p), ('count', ctypes.c_uint),
                ('unparsed_records', ctypes.c_uint)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    box = {}

    def hook(name, a):
        u = box['oracle'].uc
        if name in ('memcpy', 'memmove'):
            u.mem_write(a[0], bytes(u.mem_read(a[1], a[2])))
            return a[0]
        if name == 'memset':
            u.mem_write(a[0], bytes([a[1] & 255]) * a[2])
            return a[0]
        if name == 'plr_gponomci_module_log':
            return 0
        raise AssertionError(('unmodeled original call', name))

    o = box['oracle'] = ElfOracle(args.stock, hook, ('plr_gponomci_module_log',))
    p = o.scratch + 0x2000
    classes = set(struct.unpack('<118I', o.uc.mem_read(o.symbols['s_plrRmApi2MeCls'], 472))) - {65536}
    descriptors = {}
    for c in sorted(classes):
        o.uc.mem_write(p, bytes(32))
        if o.call('plr_gponomci_medetail_get', c, p, p + 8) != 1:
            continue
        count = o.uc.mem_read(p, 1)[0]
        table = struct.unpack('<Q', o.uc.mem_read(p + 8, 8))[0]
        rows = [bytes(o.uc.mem_read(table + i * 40, 40)) for i in range(count)]
        descriptors[c] = rows
    assert len(descriptors) == 58
    source = root / 'port/packages/tyfza1-fttr/src'
    native_widths = {int(c): [int(n) for n in widths.split(',')]
                     for c, widths in re.findall(r'\{ (\d+), \{ ([\d, ]+) \} \}',
                                                source.joinpath('omci-model.c').read_text())}
    assert native_widths == {c: [r[12] for r in rows] for c, rows in descriptors.items() if c != 53}
    duplicate_masks = [struct.unpack('<H', row[8:10])[0] for row in descriptors[53]]
    assert duplicate_masks.count(0x400) == 2 and 0x200 not in duplicate_masks

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        str(source / 'omci-model.c'), '-o', str(work / 'model.so')], check=True)
        lib = ctypes.CDLL(str(work / 'model.so'))
        lib.fttr_mib_model_add.argtypes = [ctypes.POINTER(Model), ctypes.POINTER(Record)]
        lib.fttr_mib_entity_attr.argtypes = [ctypes.c_void_p, ctypes.c_ushort, ctypes.POINTER(ctypes.c_size_t)]
        lib.fttr_mib_entity_attr.restype = ctypes.c_void_p
        lib.fttr_mib_model_dump.argtypes = [ctypes.POINTER(Model), ctypes.c_void_p]
        lib.fttr_mib_model_clear.argtypes = [ctypes.POINTER(Model)]
        model = Model()

        def add(c, instance, mask, payload):
            record = Record(c, instance, mask, (ctypes.c_ubyte * 26)(*payload.ljust(26, b'\0')))
            return lib.fttr_mib_model_add(ctypes.byref(model), ctypes.byref(record))

        def entities():
            pointer, result = model.entities, {}
            while pointer:
                entity = Entity.from_address(pointer)
                result[entity.class_id, entity.instance] = (pointer, entity)
                pointer = entity.next
            return result

        def stock_parse(c, instance, mask, payload):
            body = struct.pack('>HBBHHHHH', 0x8123, 0x2e, 10, 2, 0, c, instance, mask)
            body += payload.ljust(26, b'\0') + b'\0\0\0\x28'
            o.uc.mem_write(p, body)
            o.uc.mem_write(p + 512, b'\xa5' * 1024)
            ret = o.call('plr_gponomci_me_uploadnext_cont_parse', c, p, p + 512, 1024)
            return ret, bytes(o.uc.mem_read(p + 512, 1024))

        cases = [(256, 0, 0xc000, b'TEST' + b'version-1'.ljust(14, b'\0')),
                 (256, 0, 0x2000, bytes.fromhex('5445535401234567')),
                 (257, 0, 0xf000, b'equipment'.ljust(20, b'\0') + b'\xa4\x12\x34\x01'),
                 (262, 0x8007, 0xe000, bytes.fromhex('01230103')),
                 (277, 0x8123, 0x0600, bytes.fromhex('800700050034')),
                 (329, 0x0103, 0xc000, bytes.fromhex('0100')),
                 (65385, 2, 0xe000, b'\x01\x02' + b'example-ssid'.ljust(24, b'\0'))]
        for c, instance, mask, payload in cases:
            assert add(c, instance, mask, payload) == 0
            ret, ram = stock_parse(c, instance, mask, payload)
            assert ret == 0x80000000
            pointer, entity = entities()[c, instance]
            offset = 6
            for row in descriptors[c]:
                bit = struct.unpack('<H', row[8:10])[0]
                if mask & bit:
                    value = ram[offset:offset + row[12]]
                    if row[16:20] == bytes(4) and row[11] == row[12] and row[12] in (2, 4):
                        value = value[::-1]
                    size = ctypes.c_size_t()
                    data = lib.fttr_mib_entity_attr(pointer, bit, ctypes.byref(size))
                    assert size.value == len(value) and ctypes.string_at(data, size.value) == value
                offset += row[11]
        assert model.count == 6 and entities()[256, 0][1].present == 0xe000
        assert add(65500, 1, 0x8000, b'\x01') == 1
        assert add(257, 0, 1, b'\x02') == 1
        assert add(53, 0, 0x400, b'\x01\x02') == 1
        assert model.unparsed_records == 3 and entities()[257, 0][1].present == 0xf000
        size = ctypes.c_size_t()
        assert not lib.fttr_mib_entity_attr(entities()[256, 0][0], 0x1000, ctypes.byref(size))
        # Original success masks a failed final attribute (27 bytes), or an
        # entirely missing 30-byte attribute. Native never publishes those values.
        for c, mask in [(256, 0xf000), (384, 0x8000)]:
            ret, ram = stock_parse(c, 0, mask, bytes(range(26)))
            assert ret == 0x80000000
            assert (ram[32] == 0xa5 if c == 256 else ram[6:36] == b'\xa5' * 30)
            assert add(c, 0, mask, bytes(range(26))) == -errno.EMSGSIZE
        assert entities()[256, 0][1].present == 0xe000 and (384, 0) not in entities()
        libc = ctypes.CDLL(None)
        libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        libc.fopen.restype = ctypes.c_void_p
        libc.fclose.argtypes = [ctypes.c_void_p]
        output = work / 'model.jsonl'
        file = libc.fopen(str(output).encode(), b'w')
        assert file and lib.fttr_mib_model_dump(ctypes.byref(model), file) == 0
        assert libc.fclose(file) == 0
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        assert rows[-1] == {'event': 'mib_model', 'entities': 8, 'unparsed_records': 3}
        onu = next(r for r in rows if r.get('class') == 256)
        assert [a['mask'] for a in onu['attributes']] == [0x8000, 0x4000, 0x2000]
        lib.fttr_mib_model_clear(ctypes.byref(model))
        assert not model.entities and model.count == 0 and model.unparsed_records == 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'original_classes': 58, 'native_unambiguous_layouts': 57, 'parser_comparisons': len(cases),
        'split_records_merge': True, 'unknown_records_preserved': True,
        'original_false_success_reproduced': True, 'native_rejects_incomplete_attributes': True,
        'ambiguous_original_pots_layout_left_opaque': True, 'optical_hardware_verified': False}, indent=2) + '\n')
    print('MIB model: original wire layouts/parser, split records and false-success correction passed')


if __name__ == '__main__':
    main()
