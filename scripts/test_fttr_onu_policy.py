#!/usr/bin/env python3
"""Compare per-ONU policy decisions to both original selectors; verify durable binding."""
import argparse
import ctypes as C
import errno
import json
from pathlib import Path
import subprocess
import tempfile
from trace_stock_fttr_auth import AssignmentOriginal

ROOT = Path(__file__).resolve().parents[1]

class Credentials(C.Structure):
    _fields_ = [('serial', C.c_ubyte * 8), ('ploam', C.c_ubyte * 10),
                ('loid', C.c_char * 25), ('key', C.c_char * 13), ('ploam_valid', C.c_bool), ('loid_valid', C.c_bool)]

class Row(C.Structure):
    _fields_ = [('expected', Credentials), ('method', C.c_uint), ('present', C.c_bool),
                ('active', C.c_bool), ('learned', C.c_bool), ('bound', C.c_bool)]

class Policy(C.Structure):
    _fields_ = [('rows', Row * 64), ('vendors', (C.c_ubyte * 4) * 32), ('vendor_count', C.c_uint),
                ('enabled', C.c_bool), ('auto_learn', C.c_bool), ('auto_find', C.c_bool),
                ('directory', C.c_int), ('name', C.c_char * 40), ('document', C.c_void_p), ('bindings', C.c_void_p)]

class Selection(C.Structure):
    _fields_ = [('action', C.c_int), ('id', C.c_uint), ('method', C.c_uint), ('error', C.c_int)]

MATCH, WAIT, REJECT, MOVE, BIND, LEARN = range(6)


def credentials(serial=b'TEST\1\2\3\4', ploam=b'0123456789', loid=b'synthetic-loid', key=b'synthetic-pw'):
    return Credentials((C.c_ubyte * 8).from_buffer_copy(serial), (C.c_ubyte * 10).from_buffer_copy(ploam), loid, key, True, True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('asic', 'fpga', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    counts = dict(comparisons=0, selections=0, persistence=0)
    src = ROOT / 'port/packages/tyfza1-fttr/src'
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        subprocess.run(['cc', '-O2', '-g', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        str(src/'onu-policy.c'), str(src/'sync-json.c'), '-ljson-c', '-o', str(folder/'policy.so')], check=True)
        lib = C.CDLL(str(folder/'policy.so'))
        lib.fttr_onu_compare.argtypes = [C.c_uint, C.POINTER(Credentials), C.POINTER(Credentials)]
        lib.fttr_onu_policy_select.argtypes = [C.POINTER(Policy), C.c_uint, C.POINTER(Credentials), C.POINTER(C.c_bool)]
        lib.fttr_onu_policy_select.restype = Selection
        lib.fttr_onu_policy_load.argtypes = [C.c_char_p, C.POINTER(Policy)]
        lib.fttr_onu_policy_close.argtypes = [C.POINTER(Policy)]
        lib.fttr_onu_policy_bind.argtypes = [C.POINTER(Policy), C.POINTER(Selection), C.POINTER(C.c_ubyte)]
        normal = credentials()
        def select(p, observed=normal, hardware=5, live=None):
            return lib.fttr_onu_policy_select(C.byref(p), hardware, C.byref(observed), (C.c_bool*64)(*(live or [False]*64)))
        for binary in (args.asic, args.fpga):
            original = AssignmentOriginal(binary)
            def stockcred(c):
                return original.credentials(bytes(c.serial), bytes(c.ploam), c.loid, c.key)
            for method in range(1, 6):
                for expected in (normal, credentials(serial=bytes(8)), credentials(serial=b'\0EST\1\2\3\4'),
                                 credentials(ploam=b'01\0'+b'3'*7)):
                    for observed in (normal, credentials(serial=b'ELSE\1\2\3\4'), credentials(ploam=b'1123456789'),
                                     credentials(loid=b'other'), credentials(key=b'other'), credentials(ploam=b'01\0'+b'4'*7)):
                        assert lib.fttr_onu_compare(method, C.byref(expected), C.byref(observed)) == original.compare(method, stockcred(expected), stockcred(observed))
                        counts['comparisons'] += 1
                for slot in (2, 5):
                    for active in (False, True):
                        for bound in (False, True):
                            expected = normal if bound else credentials(serial=bytes(8))
                            p = Policy(enabled=True, directory=-1)
                            p.rows[slot] = Row(expected, method, True, active, False, False)
                            result = select(p)
                            ref = original.select({slot+1: dict(method=method, credentials=stockcred(expected), active=int(active))}, stockcred(normal))
                            action = {0:MATCH, 1: BIND if method in (2,4,5) and not bound else WAIT, 2:REJECT, 3:MOVE}[ref['returned']]
                            assert result.action == action, (method, slot, active, bound, result.action, ref)
                            if ref['logical_onu']: assert result.id + 1 == ref['logical_onu']
                            counts['selections'] += 1
            for learn, find in ((False,False), (True,False), (False,True), (True,True)):
                for occupied in (False,True):
                    p = Policy(enabled=True, directory=-1, auto_learn=learn, auto_find=find)
                    rows = {}
                    if occupied:
                        wrong = credentials(serial=b'ELSE\1\2\3\4')
                        p.rows[5] = Row(wrong, 1, True, True, False, False)
                        rows[6] = dict(method=1, credentials=stockcred(wrong))
                    result = select(p)
                    ref = original.select(rows, stockcred(normal), learn=int(learn), find=int(find))
                    assert result.action == (LEARN if ref['auto_added'] else REJECT)
                    if ref['auto_added']: assert result.id+1 == ref['logical_onu']
                    counts['selections'] += 1
        # Credential availability is explicit; neither zero values nor auto-learn bypass it.
        absent = credentials(); absent.ploam_valid = absent.loid_valid = False
        for method in range(2,6):
            p = Policy(enabled=True, directory=-1, auto_learn=True, auto_find=True)
            p.rows[5] = Row(credentials(serial=bytes(8)), method, True, True, False, False)
            assert lib.fttr_onu_compare(method, C.byref(normal), C.byref(absent)) == -errno.ENODATA
            result = select(p, absent)
            assert result.action == REJECT and result.error == -errno.ENODATA
        p = Policy(enabled=True, directory=-1)
        p.rows[2] = Row(normal,1,True,True,False,False)
        live = [False]*64; live[2] = True
        assert select(p, live=live).action == REJECT
        assert select(p).action == MOVE
        p.vendor_count = 1; p.vendors[0][:] = b'NOPE'
        assert select(p).action == REJECT
        p.vendors[0][:] = b'TEST'
        assert select(p).action == MOVE

        state = folder/'state'; state.mkdir(mode=0o700)
        path = folder/'policy.json'
        document = dict(version=1, revision='v1', state_directory=str(state), auto_learn=False, auto_find=False,
                        vendors=['54455354'], onus=[dict(id=6, method=5, active=True, serial='0'*16,
                        ploam='0'*20, loid='synthetic-loid', key='synthetic-pw')])
        def load(data=document, want=0):
            path.write_text(json.dumps(data)); path.chmod(0o600)
            p = Policy(directory=-1)
            ret = lib.fttr_onu_policy_load(str(path).encode(), C.byref(p))
            assert ret == want, (ret, want)
            counts['persistence'] += 1
            return p
        p = load()
        locked = Policy(directory=-1)
        assert lib.fttr_onu_policy_load(str(path).encode(), C.byref(locked)) == -errno.EWOULDBLOCK
        result = select(p)
        assert result.action == BIND and lib.fttr_onu_policy_bind(C.byref(p), C.byref(result), normal.serial) == 0
        assert select(p).action == MATCH and p.rows[5].bound
        assert lib.fttr_onu_policy_bind(C.byref(p), C.byref(result), normal.serial) == -errno.ESTALE
        lib.fttr_onu_policy_close(C.byref(p))
        assert (state/'v1.json').stat().st_mode & 0o777 == 0o600
        p = load(); assert p.rows[5].bound and select(p).action == MATCH
        assert select(p, credentials(serial=b'TEST\5\6\7\10')).action == REJECT
        lib.fttr_onu_policy_close(C.byref(p))
        load(dict(document, auto_find=True), -errno.ESTALE)
        for changes in (dict(version=2),dict(revision='../escape'),dict(extra=True),{'version|revision':1},dict(vendors=['5445535Z']),
                        dict(onus=document['onus']*2), dict(onus=[dict(document['onus'][0], id=0)]),
                        dict(onus=[dict(document['onus'][0], loid='x'*25)])):
            load(dict(document, **changes), -errno.EINVAL)
        learned = dict(document, revision='learn', auto_find=True, auto_learn=True, onus=[])
        p = load(learned); result = select(p); assert result.action == LEARN
        assert lib.fttr_onu_policy_bind(C.byref(p), C.byref(result), normal.serial) == 0
        lib.fttr_onu_policy_close(C.byref(p))
        p = load(learned); assert p.rows[5].method == 1 and p.rows[5].learned and select(p).action == MATCH
        lib.fttr_onu_policy_close(C.byref(p))
        # A failure before rename cannot install the new in-memory binding.
        failed = dict(learned, revision='fail')
        p = load(failed); result = select(p)
        (state/'fail.json.new').mkdir()
        assert lib.fttr_onu_policy_bind(C.byref(p), C.byref(result), normal.serial) < 0
        assert not p.rows[5].present
        lib.fttr_onu_policy_close(C.byref(p))
        (state/'fail.json.new').rmdir()
        p = load(failed); assert not p.rows[5].present
        lib.fttr_onu_policy_close(C.byref(p))
        path.chmod(0o644)
        assert lib.fttr_onu_policy_load(str(path).encode(), C.byref(p)) == -errno.EACCES
        state.chmod(0o755); load(learned, -errno.EACCES); state.chmod(0o700)
        persisted=json.loads((state/'learn.json').read_text());persisted['bindings'][5]='0'*16
        (state/'learn.json').write_text(json.dumps(persisted))
        load(learned,-errno.EBADMSG)
    args.output.write_text(json.dumps(dict(**counts, missing_credentials='rejected', persistence_fault='no binding', hardware_tested=False),indent=2)+'\n')
    print('PASS:', counts, 'original selectors, explicit missing credentials and durable policy bindings')

if __name__ == '__main__': main()
