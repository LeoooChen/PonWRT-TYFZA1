#!/usr/bin/env python3
"""Exercise the real Wi-Fi command over veth against original Set wire bytes.

Run inside a fresh network namespace. Only the peer MIB and MiniOLT attributes
are modeled; no optical hardware or subordinate wireless execution is claimed.
"""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from trace_stock_fttr_wifi import WifiOriginal, config
from test_fttr_controller import HOST, OLT


def policy(cfg):
    numbers = dict(instance=0x101, mode=cfg[3], channel=cfg[4], bandwidth=cfg[37],
                   security=cfg[46], encryption=cfg[242], key_auth=cfg[111],
                   key_index=cfg[113], ssid_enabled=cfg[245], radio_enabled=cfg[246])
    values = dict(ssid_hex=cfg[5:29], shared_key_hex=cfg[47:71])
    values.update({f'key{i + 1}_hex': cfg[offset:offset + 26]
                   for i, offset in enumerate((114, 146, 178, 210))})
    return ''.join(f'{key} {value}\n' for key, value in numbers.items()) + ''.join(
        f'{key} {value.hex()}\n' for key, value in values.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(
        ['ip', '-j', 'link'], text=True))} == {'lo'}, 'requires a fresh network namespace'
    stock = WifiOriginal(args.master)
    widths = {}
    for cls in (0xff69, 0xff6a):
        o, p = stock.oracle, stock.data
        assert o.call('plr_gponomci_medetail_get', cls, p, p + 8) == 1
        table = struct.unpack('<Q', o.uc.mem_read(p + 8, 8))[0]
        widths[cls] = {struct.unpack('<H', o.uc.mem_read(table + i * 40 + 8, 2))[0]:
                       o.uc.mem_read(table + i * 40 + 12, 1)[0]
                       for i in range(o.uc.mem_read(p, 1)[0])}
    subprocess.run(['ip', 'link', 'add', 'fw-host', 'type', 'veth', 'peer', 'name', 'fw-peer'], check=True)
    for interface, address in [('fw-host', HOST), ('fw-peer', OLT)]:
        subprocess.run(['ip', 'link', 'set', interface, 'address', address.hex(':'),
                        'mtu', '1982', 'up'], check=True)
    peer = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x8888))
    peer.bind(('fw-peer', 0x8888))
    peer.setblocking(False)
    checks = []
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        source = ROOT / 'port/packages/tyfza1-fttr/src'
        control = directory / 'fttrctl'
        subprocess.run(['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-o', str(control),
                        str(source / 'fttrctl.c'), str(source / 'miniolt.c'),
                        *map(str, sorted(source.glob('omci-*.c')))], check=True)
        device = directory / 'miniolt'
        device.mkdir()
        (device / 'omci_link').write_text('host=02:03:04:05:06:07 olt=0a:0b:0c:0d:0e:0f '
                                        'ethertype=8888 downstream_length=0\n')
        (device / 'management_grant').write_text('')
        config_file = directory / 'wifi.conf'
        config_file.touch(mode=0o600)

        def hardware(replaced=False):
            serial = b'TEST\0\0\0' + bytes([8 if replaced else 5])
            (device / 'onu_state').write_bytes(b''.join(
                struct.pack('<III8s', 1 << 18 if i == 5 else 0, 1, 42, serial)
                for i in range(64)))

        def run(cfg, case='normal', before=None, text=None):
            expected = stock.run_wifi(cfg)
            desired = {(struct.unpack('>H', b[4:6])[0], struct.unpack('>H', b[8:10])[0]): b[10:]
                       for b in expected}
            sizes = {(cls, mask): sum(w for bit, w in widths[cls].items() if bit & mask)
                     for cls, mask in desired}
            values = {key: bytes([0x55]) * size for key, size in sizes.items()} if before is None else dict(before)
            original = dict(values)
            config_file.write_text(policy(cfg) if text is None else text)
            hardware()
            command = [str(control), str(device), 'wifi', '5', str(config_file), 'fw-host']
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            operations, writes, snapshot = [], [], []
            deadline = time.monotonic() + 18
            changed = False
            lost_tci = None
            try:
                while process.poll() is None:
                    assert time.monotonic() < deadline, ('command stalled', case, operations)
                    ready, _, _ = select.select([peer], [], [], 0.02)
                    if not ready:
                        continue
                    frame, address = peer.recvfrom(2048)
                    if address[2] == socket.PACKET_OUTGOING:
                        continue
                    assert frame[:16] == OLT + HOST + b'\x88\x88\0\5'
                    body = frame[16:60]
                    tci, op, dev, cls, instance, mask = struct.unpack('>HBBHHH', body[:10])
                    assert dev == 10
                    operations.append((op, cls, mask, tci))
                    if changed:
                        raise AssertionError(('transmission after replacement', operations))
                    status, content = 0, b''
                    if op == 0x4d:
                        snapshot = [(c, 0x101, 0x8000, bytes([0])) for c in (0xff69, 0xff6a)
                                    if not (case == 'missing-mib' and c == 0xff6a)]
                        content = struct.pack('>H', len(snapshot))
                    elif op == 0x4e:
                        c, inst, bits, data = snapshot[mask]
                        content = struct.pack('>HHH', c, inst, bits) + data.ljust(26, b'\0')
                        if case == 'replace-inventory' and mask == len(snapshot) - 1:
                            hardware(True)
                            changed = True
                    elif op == 0x49:
                        assert instance == 0x101
                        if case == 'timeout' and not writes:
                            continue
                        status = 5 if case == 'missing-group' and (cls, mask) == list(desired)[-1] else 0
                        content = bytes([status]) + struct.pack('>H', mask) + values[cls, mask]
                        if case == 'replace-completion' and len(writes) == len(expected) and (cls, mask) == list(desired)[-1]:
                            hardware(True)
                            changed = True
                    else:
                        assert op == 0x48 and instance == 0x101
                        value = body[10:10 + sizes[cls, mask]]
                        restoring = value == original[cls, mask]
                        writes.append(body)
                        reject = case in ('reject', 'restore-rejected', 'replace-cleanup') and len(writes) == 3
                        if reject or (restoring and case == 'restore-rejected'):
                            status = 2
                        else:
                            values[cls, mask] = value
                            if case == 'bad-readback' and len(writes) == 2:
                                values[cls, mask] = bytes([0xee]) * len(value)
                        if case == 'replace' or (case == 'replace-cleanup' and restoring):
                            hardware(True)
                            changed = True
                            continue
                        if case == 'lost-set' and len(writes) == 2:
                            lost_tci = tci
                        if tci == lost_tci:
                            continue
                        content = bytes([status])
                    reply = stock.frame(tci, (op & 31) | 32, content, 5, cls, instance)
                    if case in ('unrelated', 'malformed-unrelated'):
                        # Other ONU and stale TCI responses cannot complete the request.
                        peer.send(stock.frame(tci, (op & 31) | 32, content, 7, cls, instance))
                        peer.send(stock.frame(tci ^ 0x100, (op & 31) | 32, content, 5, cls, instance))
                    if case == 'malformed-unrelated' and cls != 2:
                        for other_onu, other_tci in ((7, tci), (5, tci ^ 0x100)):
                            malformed = bytearray(stock.frame(other_tci, (op & 31) | 32, content,
                                                              other_onu, cls, instance))
                            malformed[19] = 0xff
                            peer.send(malformed)
                        peer.send((HOST + OLT + b'\x88\x88\0\7').ljust(60, b'\0'))
                    if case == 'malformed-matching' and op == 0x48 and len(writes) == 2:
                        reply = bytearray(reply)
                        reply[19] = 0xff
                    peer.send(reply)
                output, errors = process.communicate(timeout=1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            result = json.loads(output)
            assert result['event'] == 'wifi_state'
            for sensitive in ('FTTR native research', 'test-passphrase', cfg[5:29].hex(), cfg[47:71].hex(),
                              '"request"', '"reply"', '"previous"'):
                assert sensitive not in output + errors
            if text is not None:
                assert process.returncode == 1 and not operations and result['error'] < 0
            elif case in ('normal', 'unrelated', 'malformed-unrelated'):
                assert process.returncode == 0 and result['configured'], (result, errors)
                if before is None:
                    assert [b[2:] for b in writes] == [b[2:] for b in expected]
                    assert [op for op, cls, _, _ in operations if cls != 2][:len(expected)] == [0x49] * len(expected)
                else:
                    assert not writes
            else:
                assert process.returncode == 1 and not result['configured']
                if case in ('reject', 'bad-readback', 'malformed-matching', 'lost-set'):
                    assert values == original and not result['cleanup_error']
                    if case == 'lost-set':
                        assert result['error'] == -errno.ETIMEDOUT
                        assert len([row for row in operations if row[3] == lost_tci]) == 3
                elif case == 'restore-rejected':
                    assert result['error'] == -errno.EREMOTEIO and result['cleanup_error'] == -errno.EREMOTEIO
                    assert any(g['touched'] for g in result['groups'])
                elif case == 'replace-cleanup':
                    assert result['error'] == -errno.EREMOTEIO and result['cleanup_error'] == -errno.ESTALE
                elif case == 'replace':
                    assert result['error'] == -errno.ESTALE and len(writes) == 1
                elif case in ('replace-inventory', 'replace-completion'):
                    assert result['error'] == -errno.ESTALE
                    assert len(writes) == (0 if case == 'replace-inventory' else len(expected))
                elif case == 'timeout':
                    gets = [row for row in operations if row[0] == 0x49]
                    assert len(gets) == 3 and len({row[3] for row in gets}) == 1
                    assert result['error'] == -errno.ETIMEDOUT and not writes
                else:
                    assert not writes
            return values

        for mode, security in ((0, 0), (3, 1), (7, 3), (4, 5), (6, 6)):
            cfg = config(mode, security)
            values = run(cfg)
            run(cfg, before=values)
        checks.append('five original mode/security branches match real CLI Set frames; repeated configuration writes nothing')
        for case in ('unrelated', 'malformed-unrelated', 'malformed-matching', 'missing-mib', 'missing-group', 'reject', 'bad-readback',
                     'restore-rejected', 'timeout', 'lost-set', 'replace', 'replace-cleanup',
                     'replace-inventory', 'replace-completion'):
            run(config(), case)
            checks.append(case)
        normal = policy(config())
        for invalid in (normal + 'mode 3\n', normal + 'unknown 1\n',
                        normal.replace('channel 6', 'channel 256'),
                        normal.replace('radio_enabled 1', 'radio_enabled 2'),
                        normal.replace('mode 3', 'mode -1'),
                        normal.replace('mode 3', 'mode 3\0ignored'),
                        normal.replace('ssid_hex ' + config()[5:29].hex(), 'ssid_hex ' + 'ab' * 25),
                        normal.replace('shared_key_hex ' + config()[47:71].hex(), 'shared_key_hex ' + 'ab' * 25),
                        normal.replace('ssid_hex ' + config()[5:29].hex(), 'ssid_hex abc'),
                        normal.replace('ssid_hex ' + config()[5:29].hex(), 'ssid_hex gg'),
                        '\n'.join(line for line in normal.splitlines() if not line.startswith('shared_key_hex ')),
                        policy(config(4, 5)).replace('key4_hex ' + '44' * 26 + '\n', '')):
            run(config(), text=invalid)
        checks.append('invalid, duplicate, missing, embedded-NUL and oversized fields reject before management traffic')
        lease = os.open(device, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(config(), text=normal)
        finally:
            os.close(lease)
        checks.append('existing controller lease rejects command without traffic')
        checks.append('all diagnostics omit SSIDs, keys, prior values and raw exchanges')
    peer.close()
    result = {'stock_sha256': hashlib.sha256(args.master.read_bytes()).hexdigest(), 'checks': checks,
              'scope': 'Actual CLI/AF_PACKET/veth, original master producers/encoders/descriptors; modeled peer MIB and sysfs. No optical or wireless acceptance.'}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
