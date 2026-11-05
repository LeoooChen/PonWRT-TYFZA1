#!/usr/bin/env python3
"""Verify native global LOID transactions against original branches and wire bytes."""
import argparse
import ctypes as C
import errno
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from test_fttr_gem import Me, Message
from test_fttr_mib import Stock
from test_fttr_vlan import Slave
from arm64_elf_oracle import ElfOracle
from trace_stock_fttr_auth import AuthOriginal

ROOT = Path(__file__).resolve().parents[1]


class Config(C.Structure):
    _fields_ = [('loid', C.c_char * 25), ('key', C.c_char * 13), ('enabled', C.c_bool)]


class Job(C.Structure):
    _fields_ = [('me', Me), ('phase', C.c_int), ('loid', C.c_char * 25), ('result', C.c_ubyte),
               ('acquired', C.c_bool), ('verified', C.c_bool), ('error', C.c_int), ('fallback_error', C.c_int)]


class LoidSlave(Slave):
    """Original GET worker and actual LoidAuth metadata, modeled result storage."""
    def __init__(self, binary):
        def metadata(name, a):
            if name == 'MIB_InfoRegister': return 0
            if name == 'memset': table.uc.mem_write(a[0], bytes([a[1] & 255])*a[2]); return a[0]
            if name == 'strcpy':
                data = bytes(table.uc.mem_read(a[1], 128)).split(b'\0')[0] + b'\0'
                table.uc.mem_write(a[0], data); return a[0]
            raise AssertionError(name)
        table = ElfOracle(binary.parent.parent/'lib/omci/mib_LoidAuth.so', metadata)
        assert table.call('mibTable_init', 1) == 0
        self.attrs = [bytes(table.uc.mem_read(table.symbols['gMibLoIdAuthAttrInfo']+i*48, 48)) for i in range(5)]
        super().__init__(binary)
        del self.oracle.hooks[0x13a54]  # Execute the original mask/capability selection.
        self.result = 0

    def handler(self, name, a):
        if name == 'MIB_GetTableIndexByClassId': assert a[0] == 65530; return 1
        if name == 'MIB_GetTableEntrySize': return 46
        if name == 'MIB_GetTableAttrNum': return 5
        if name in ('MIB_GetAttrOptionType', 'MIB_GetAttrLen', 'MIB_GetAttrDataType'):
            offset = {'MIB_GetAttrOptionType':32, 'MIB_GetAttrLen':20, 'MIB_GetAttrDataType':16}[name]
            return struct.unpack_from('<I', self.attrs[a[1]-1], offset)[0]
        if name == 'MIB_GetAttrFromBuf':
            assert a[1] == 5 and a[4] == 1
            self.oracle.uc.mem_write(a[2], bytes([self.result])); return 0
        if name == 'OMCI_MeOperCfg': return 0
        return super().handler(name, a)


def private_file(path, loid, key):
    path.write_bytes(b'TFL1' + loid.ljust(25, b'\0') + key.ljust(13, b'\0'))
    path.chmod(0o600)


def factory(args, folder):
    source, out = folder / 'factory', folder / 'import'
    source.mkdir(); out.mkdir()
    for name in ('config_hs.xml', 'config.xml', 'rtkbosa_k.bin', 'rtkoltbosa_k.bin'):
        if (args.factory / name).exists():
            shutil.copyfile(args.factory / name, source / name)
    cs = (source / 'config.xml').read_bytes()
    for field, placeholder in [(b'CTC_FTTR_MASTER_GLOBAL_LOID', b'TEST_AUTH_LOID'),
            (b'CTC_FTTR_MASTER_GLOBAL_LOID_PASSWD', b'TEST_AUTH_KEY')]:
        prefix = b'<Value Name="' + field + b'" Value="'
        pattern = rb'<Value\s+Name="' + field + rb'"\s+Value="[^"]*"'
        cs, count = re.subn(pattern, prefix + placeholder + b'"', cs)
        if not count: cs += prefix + placeholder + b'"/>\n'
    cmd = [str(args.ucode), '-L', str(args.ucode_modules),
        str(ROOT / 'port/packages/tyfza1-pon-control/files/factory.uc'), str(source), str(out)]
    target = out / 'tmp/tyfza1-factory/fttr-private/loid.bin'
    for loid, key in [(b'', b''), (b'one', b''), (b'', b'two'), (b'L'*24, b'K'*12), (b'a&amp;b', b'x&lt;y')]:
        (source / 'config.xml').write_bytes(cs.replace(b'TEST_AUTH_LOID', loid).replace(b'TEST_AUTH_KEY', key))
        proc = subprocess.run(cmd, capture_output=True)
        assert proc.returncode == 0, 'factory fixture failed'
        assert target.read_bytes() == b'TFL1' + loid.replace(b'&amp;', b'&').ljust(25, b'\0') + key.replace(b'&lt;', b'<').ljust(13, b'\0')
        assert target.stat().st_mode & 0o777 == 0o600 and target.parent.stat().st_mode & 0o777 == 0o700
    for loid, key in [(b'L'*25, b'K'), (b'L', b'K'*13)]:
        (source / 'config.xml').write_bytes(cs.replace(b'TEST_AUTH_LOID', loid).replace(b'TEST_AUTH_KEY', key))
        proc = subprocess.run(cmd, capture_output=True)
        assert proc.returncode != 0 and not target.exists()
    (source / 'config.xml').write_bytes(cs.replace(b'CTC_FTTR_MASTER_GLOBAL_', b'ABSENT_'))
    assert subprocess.run(cmd, capture_output=True).returncode == 0
    assert target.read_bytes() == b'TFL1' + bytes(38)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('master', 'fpga', 'slave', 'factory', 'ucode', 'ucode-modules', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    stock = Stock(args.master)
    # CTC credentials use the original urgent TX queue; observe its output
    # through the existing packet observer without executing queue machinery.
    stock.oracle.hooks[stock.oracle.symbols['plr_gponomci_me_urgent_prio_tx']] = 'plr_gponomci_me_high_prio_tx'
    original = AuthOriginal(args.master)
    fpga = AuthOriginal(args.fpga)
    slave = LoidSlave(args.slave)
    src = ROOT / 'port/packages/tyfza1-fttr/src'
    cases = []
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        factory(args, folder)
        subprocess.run(['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
            *[str(src / n) for n in ('omci-auth.c', 'omci-me.c', 'omci-socket.c', 'omci-wire.c', 'miniolt.c')],
            '-o', str(folder / 'auth.so')], check=True)
        native = C.CDLL(str(folder / 'auth.so'))
        native.fttr_auth_config_read.argtypes = [C.c_char_p, C.POINTER(Config)]
        native.fttr_auth_start.argtypes = [C.POINTER(Job), C.POINTER(Config), C.c_ushort, C.POINTER(C.c_ushort)]
        native.fttr_auth_advance.argtypes = [C.POINTER(Job), C.POINTER(Config), C.POINTER(C.c_ushort)]
        native.fttr_auth_cancel.argtypes = [C.POINTER(Job), C.c_int]
        native.fttr_me_job_reply.argtypes = [C.POINTER(Me), C.POINTER(Message)]
        path = folder / 'policy'
        for loid, key in [(b'', b''), (b'L', b''), (b'', b'K'), (b'L'*24, b'K'*12)]:
            private_file(path, loid, key)
            cfg = Config()
            assert native.fttr_auth_config_read(str(path).encode(), C.byref(cfg)) == 0
            assert cfg.loid == loid and cfg.key == key and cfg.enabled == bool(loid and key)
        good = path.read_bytes()
        for data in (b'', good[:-1], good+b'X', b'FAIL'+good[4:], good[:28]+b'X'+good[29:], good[:-1]+b'X'):
            path.write_bytes(data)
            assert native.fttr_auth_config_read(str(path).encode(), C.byref(cfg)) < 0 and not cfg.enabled
        path.write_bytes(good); path.chmod(0o644)
        assert native.fttr_auth_config_read(str(path).encode(), C.byref(cfg)) == -errno.EACCES
        path.chmod(0o600); alias = folder / 'alias'; alias.symlink_to(path)
        assert native.fttr_auth_config_read(str(alias).encode(), C.byref(cfg)) == -errno.ELOOP
        assert native.fttr_auth_config_read(str(folder).encode(), C.byref(cfg)) < 0
        cfg = Config(b'synthetic-loid', b'synthetic-pw', True)

        def run(label, loid=b'synthetic-loid', key=b'synthetic-pw', failures=None, malformed=None, wrong=False):
            failures = failures or {}
            job, counter = Job(), C.c_ushort(0xffff)
            assert native.fttr_auth_start(C.byref(job), C.byref(cfg), 5, C.byref(counter)) == 0
            operations, written = [], []
            while job.phase not in (7, 8):
                body = bytes(job.me.request)
                tci, op, dev, cls, instance, mask = struct.unpack('>HBBHHH', body[:10])
                assert dev == 10 and instance == 0 and job.me.onu == 5
                ram = bytes(40) + bytes([job.result]) if cls == 65530 else bytes(71)
                reference = stock.me_request(op, cls, instance, mask, ram)
                assert body[2:] == reference[2:]
                operations.append((op, cls, mask))
                fault = failures.get((op, cls, mask), 0)
                if fault in (-errno.ETIMEDOUT, -errno.EIO, -errno.ESTALE):
                    job.me.state = 2; job.me.error = fault
                else:
                    if op == 0x48:
                        written.append(body[10]); value = b''
                    elif mask == 0x1000: value = bytes([0 if wrong else job.result])
                    elif mask in (0x4000, 0x0040): value = loid.ljust(24, b'\0')
                    else: value = key.ljust(12, b'\0')
                    content = bytes([fault]) + (struct.pack('>H', mask) + value if op == 0x49 else b'')
                    reply = bytearray(stock.frame(tci, (op & 31) | 32, content, 5, cls, instance)[16:60])
                    if op == 0x49 and cls == 65530 and mask == 0x1000 and not fault:
                        slave.result = value[0]
                        original_reply = slave.reply(body, b'', 0)[:44]
                        assert bytes(reply) == original_reply
                        reply = bytearray(original_reply)
                    if malformed == (op, cls, mask): reply[3] = 0xff
                    buf = C.create_string_buffer(bytes(reply)); msg = Message(5, C.cast(buf, C.c_void_p), 44)
                    assert native.fttr_me_job_reply(C.byref(job.me), C.byref(msg)) != 0
                assert native.fttr_auth_advance(C.byref(job), C.byref(cfg), C.byref(counter)) in (0, 1)
                assert len(operations) <= 6
            assert not job.loid and bytes(job.me.reply) == bytes(44)
            if job.phase == 7:
                if label == 'maximum-width':
                    expected = 1
                    for ref in (original, fpga):
                        value = ref.credentials(loid=loid, key=key)
                        assert ref.compare(5, value, value) == 0
                else:
                    expected = original.global_check(1, loid.split(b'\0')[0], key.split(b'\0')[0])[1]
                    assert fpga.global_check(1, loid.split(b'\0')[0], key.split(b'\0')[0])[1] == expected
                assert job.result == expected and written == [expected] and job.verified and job.acquired
                assert job.error == (0 if expected == 1 else -errno.EACCES)
            else:
                assert job.error < 0 and not job.verified
            cases.append(dict(case=label, operations=operations, phase=job.phase, result=job.result,
                acquired=bool(job.acquired), verified=bool(job.verified), error=job.error, fallback_error=job.fallback_error))
            return job, operations

        for label, loid, key in [('match', b'synthetic-loid', b'synthetic-pw'), ('loid-mismatch', b'other', b'synthetic-pw'),
                ('key-mismatch', b'synthetic-loid', b'other'), ('empty-peer', b'', b'')]:
            run(label, loid, key)
        run('fallback-loid', failures={(0x49, 65530, 0x4000): 5})
        run('fallback-key', failures={(0x49, 65530, 0x2000): 5})
        run('fallback-timeout', failures={(0x49, 65530, 0x4000): -errno.ETIMEDOUT})
        for mask in (0x0040, 0x0020):
            job, ops = run('both-paths-fail', failures={(0x49, 65530, 0x4000): 5, (0x49, 256, mask): 5})
            assert not job.acquired and not any(op == 0x48 for op, _, _ in ops)
        for failure in (-errno.EIO, -errno.ESTALE):
            job, ops = run('local-failure', failures={(0x49, 65530, 0x4000): failure})
            assert len(ops) == 1 and job.error == failure
        job, _ = run('bad-result-readback', wrong=True)
        assert job.error == -errno.EUCLEAN
        for op in (0x48, 0x49):
            job, _ = run('result-io-failure', failures={(op, 65530, 0x1000): 2})
            assert job.error == -errno.EREMOTEIO
        for request in [(0x49, 65530, 0x4000), (0x49, 65530, 0x2000), (0x48, 65530, 0x1000)]:
            job, _ = run('malformed', malformed=request)
            assert job.error == -errno.EPROTO
        cfg = Config(b'L'*24, b'K'*12, True)
        # Maximum widths must retain their final byte and append a host NUL.
        job, _ = run('maximum-width', b'L'*24, b'K'*12)
        assert job.result == 1
        cfg = Config(b'synthetic-loid', b'synthetic-pw', False)
        job = Job(); counter = C.c_ushort(0x8123)
        assert native.fttr_auth_start(C.byref(job), C.byref(cfg), 5, C.byref(counter)) == 0
        assert job.phase == 0 and not job.acquired and not job.verified and counter.value == 0x8123
    args.output.write_text(json.dumps(dict(cases=cases, factory='passed', config_permissions='passed',
        disabled_no_traffic=True, original_wire='passed', original_slave_result_get='passed',
        hardware_tested=False), indent=2) + '\n')
    print('PASS: native LOID transactions, original wire/global results, fallback/failures and factory private input')


if __name__ == '__main__':
    main()
