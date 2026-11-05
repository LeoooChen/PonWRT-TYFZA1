#!/usr/bin/env python3
"""Execute original ONU credential comparison, LOID acquisition and result paths.

All inputs are synthetic. Reports contain only branches, masks and outcomes.
No physical IO, original credentials or native authentication acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_elf_oracle import ElfOracle


class AuthOriginal:
    def __init__(self, binary):
        self.events, self.failures = [], {}
        self.data_error, self.global_enabled = 0, 0
        self.o = ElfOracle(binary, self.hook, ('plr_gponrm_pr_ctc_loid_auth_get',
            'plr_gponrm_pr_ctc_loid_auth_set', 'plr_gponrm_pr_onug_get', 'plr_ponsrv_pon_data_get'))
        self.p = self.o.scratch + 0x4000

    def string(self, address):
        return bytes(self.o.uc.mem_read(address, 128)).split(b'\0')[0]

    def hook(self, name, a):
        u = self.o.uc
        if name in ('memmove', 'memcpy'):
            u.mem_write(a[0], bytes(u.mem_read(a[1], a[2])))
            return a[0]
        if name == 'memset':
            u.mem_write(a[0], bytes([a[1] & 255]) * a[2])
            return a[0]
        if name in ('strcmp', 'memcmp'):
            if name == 'strcmp':
                left, right = self.string(a[0]), self.string(a[1])
            else:
                left, right = bytes(u.mem_read(a[0], a[2])), bytes(u.mem_read(a[1], a[2]))
            return (left > right) - (left < right)
        if name == 'plr_ponsrv_pon_data_get':
            if self.data_error:
                return self.data_error
            table = self.p + 0x1000
            u.mem_write(table, bytes(256))
            u.mem_write(table + 60, struct.pack('<I', self.global_enabled))
            u.mem_write(table + 64, b'synthetic-loid\0')
            u.mem_write(table + 89, b'synthetic-pw\0')
            u.mem_write(a[1], struct.pack('<Q', table))
            return 0
        if name in ('plr_gponrm_pr_ctc_loid_auth_get', 'plr_gponrm_pr_onug_get'):
            cls = 65530 if 'ctc' in name else 256
            instance = struct.unpack('<H', u.mem_read(a[2], 2))[0]
            self.events.append(['get', cls, instance, a[1]])
            ret = self.failures.get((cls, a[1]), 0)
            if not ret:
                if cls == 65530:
                    offset, size = {0x4000: (10, 24), 0x2000: (34, 12)}[a[1]]
                else:
                    offset, size = {0x0040: (38, 24), 0x0020: (62, 12)}[a[1]]
                u.mem_write(a[2] + offset, (b'synthetic-loid' if size == 24 else b'synthetic-pw').ljust(size, b'\0'))
            return ret
        if name == 'plr_gponrm_pr_ctc_loid_auth_set':
            instance = struct.unpack('<H', u.mem_read(a[2], 2))[0]
            self.events.append(['set', 65530, instance, a[1], u.mem_read(a[2] + 46, 1)[0]])
            return self.failures.get((65530, a[1]), 0)
        if name in ('printf', 'plr_log'):
            return 0
        raise AssertionError(('unmodeled call', name))

    def credentials(self, serial=b'TEST\1\2\3\4', ploam=b'0123456789', loid=b'synthetic-loid', key=b'synthetic-pw'):
        return serial.ljust(9, b'\0') + ploam.ljust(11, b'\0') + loid.ljust(25, b'\0') + key.ljust(13, b'\0') + b'\0'

    def compare(self, method, expected, observed):
        address = self.o.call('plr_ponsrv_auth_check_func_get', method)
        if not address:
            return None
        self.o.symbols['comparison'] = address
        self.o.uc.mem_write(self.p, expected)
        self.o.uc.mem_write(self.p + 256, observed)
        return self.o.call('comparison', self.p, self.p + 256)

    def loid_get(self, failures):
        self.failures, self.events = failures, []
        u, p = self.o.uc, self.p
        u.mem_write(p, bytes(1024))
        u.mem_write(p, struct.pack('<HH', 0, 5))
        u.mem_write(p + 128, struct.pack('<B7xQB7xQB7x', 24, p + 256, 12, p + 512, 0))
        ret = self.o.call('plr_gponrm_onu_loid_get', p, p + 128)
        return ret, self.string(p + 256), self.string(p + 512), u.mem_read(p + 160, 1)[0], list(self.events)

    def loid_set(self, result, failure):
        self.failures, self.events = {(65530, 0x1000): failure}, []
        self.o.uc.mem_write(self.p, struct.pack('<HH', 0, 5) + bytes(252))
        self.o.uc.mem_write(self.p + 160, bytes([result]))
        ret = self.o.call('plr_gponrm_onu_loid_auth_set', self.p, self.p + 128)
        assert self.events == [['set', 65530, 0, 0x1000, result]]
        return ret

    def global_check(self, enabled, loid, key, failure=0):
        self.global_enabled, self.data_error = enabled, failure
        self.o.uc.mem_write(self.p, loid + b'\0')
        self.o.uc.mem_write(self.p + 128, key + b'\0')
        self.o.uc.mem_write(self.p + 256, b'\xa5')
        ret = self.o.call('plr_ponsrv_pon_loid_auth_check', 1, self.p, self.p + 128, self.p + 256)
        return ret, self.o.uc.mem_read(self.p + 256, 1)[0]


def verify(binary):
    stock = AuthOriginal(binary)
    comparison_cases = []
    normal = stock.credentials()
    # Each comparator's exact/mismatch/bind-SN outcomes, including ignored
    # NUL/padding bytes versus fixed-width PLOAM credential comparisons.
    variants = {'same': normal, 'serial': stock.credentials(serial=b'ELSE\1\2\3\4'),
        'ploam': stock.credentials(ploam=b'1123456789'), 'loid': stock.credentials(loid=b'other'),
        'key': stock.credentials(key=b'other')}
    for method in range(1, 6):
        for kind, observed in variants.items():
            want = 2 if kind == 'serial' or (method in (2, 3) and kind == 'ploam') or (
                method in (4, 5) and kind == 'loid') or (method == 5 and kind == 'key') else 0
            result = stock.compare(method, normal, observed)
            assert result == want, (method, kind, result)
            comparison_cases.append(dict(method=method, variant=kind, result=result))
        empty_sn = stock.credentials(serial=bytes(8))
        result = stock.compare(method, empty_sn, normal)
        assert result == (1 if method in (2, 4, 5) else 2)
        comparison_cases.append(dict(method=method, variant='unbound-serial', result=result))
        first_zero = stock.credentials(serial=b'\0EST\1\2\3\4')
        result = stock.compare(method, first_zero, normal)
        assert result == (1 if method in (2, 4, 5) else 2)
        comparison_cases.append(dict(method=method, variant='first-byte-zero', result=result))
        padded_base = stock.credentials(key=b'key')
        padding = bytearray(padded_base)
        for offset in (8, 19, 44, 57):
            padding[offset] = 0xa5
        # LOID/key are strcmp fields: bytes after their earlier NUL do not
        # participate. SN/PLOAM use8/10 bytes, excluding their padding byte.
        result = stock.compare(method, padded_base, bytes(padding))
        assert result == 0
        comparison_cases.append(dict(method=method, variant='ignored-padding', result=result))
        embedded = stock.credentials(ploam=b'01\0' + b'3' * 7)
        changed = stock.credentials(ploam=b'01\0' + b'4' * 7)
        result = stock.compare(method, embedded, changed)
        assert result == (2 if method in (2, 3) else 0)
        comparison_cases.append(dict(method=method, variant='fixed-width-after-nul', result=result))
    assert stock.compare(0, normal, normal) is None and stock.compare(6, normal, normal) is None
    global_cases = []
    for enabled in (0, 1):
        for loid_ok in (False, True):
            for key_ok in (False, True):
                ret, result = stock.global_check(enabled, b'synthetic-loid' if loid_ok else b'other',
                    b'synthetic-pw' if key_ok else b'other')
                want = 0 if not enabled else 1 if loid_ok and key_ok else 2 if not loid_ok else 3
                assert result == want and ret == (1 if want < 2 else 0)
                global_cases.append(dict(enabled=enabled, loid_match=loid_ok, key_match=key_ok, result=result, returned=ret))
    assert stock.global_check(1, b'', b'', 1) == (0, 0xa5)
    loid_cases = []
    cases = [({}, True, True), ({(65530, 0x4000): 55}, True, True),
        ({(65530, 0x2000): 55}, True, True),
        ({(65530, 0x4000): 55, (256, 0x0040): 55}, False, False),
        ({(65530, 0x4000): 55, (256, 0x0020): 55}, True, False),
        ({(65530, 0x2000): 55, (256, 0x0040): 55}, True, False)]
    for failures, has_loid, has_key in cases:
        ret, loid, key, result, events = stock.loid_get(failures)
        assert ret == 0 and result == 6
        assert bool(loid) == has_loid and bool(key) == has_key
        assert not loid or loid == b'synthetic-loid'
        assert not key or key == b'synthetic-pw'
        expected = [['get', 65530, 0, 0x4000]]
        if (65530, 0x4000) not in failures:
            expected.append(['get', 65530, 0, 0x2000])
        if failures:
            expected.append(['get', 256, 0, 0x0040])
            if (256, 0x0040) not in failures:
                expected.append(['get', 256, 0, 0x0020])
        assert events == expected
        loid_cases.append(dict(failures=[list(k) + [v] for k, v in failures.items()], returned=ret,
            loid_present=has_loid, key_present=has_key, result=result, operations=events))
    set_cases = []
    for result in (1, 2, 3):
        for failure in (0, 13, 55):
            assert stock.loid_set(result, failure) == failure
            set_cases.append(dict(result=result, injected_return=failure, operations=list(stock.events)))
    return dict(sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), comparisons=comparison_cases,
        global_checks=global_cases, loid_reads=loid_cases, result_writes=set_cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asic', required=True, type=Path)
    parser.add_argument('--fpga', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = dict(asic=verify(args.asic), fpga=verify(args.fpga),
        findings=['methods1..5 compare SN, PLOAM credential, SN+credential, LOID, LOID+key',
            'methods2/4/5 return bind-SN result1 only when configured SN starts with zero',
            'LOID reads prefer fffa:4000/2000 then fall back to100:0040/0020',
            'original lower LOID getter returns0 with absent or partially populated credentials after IO failures',
            'global LOID result0 disabled,1 accepted,2 LOID mismatch,3 key mismatch',
            'LOID result writes fffa instance0 mask1000 and propagates errors'],
        scope='Actual original ARM64 routines with synthetic credentials and modeled OMCI/table IO. No native auth policy or hardware acceptance.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({backend: {key: len(result[backend][key]) for key in
        ('comparisons', 'global_checks', 'loid_reads', 'result_writes')} for backend in ('asic', 'fpga')}))


if __name__ == '__main__':
    main()
