#!/usr/bin/env python3
"""Exercise resident syncserv scheduling and identity retirement on AF_PACKET/veth."""
import argparse
import copy
import errno
import json
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from trace_stock_fttr_wifi_fields import WifiFieldsOriginal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager', type=Path, required=True)
    parser.add_argument('--mib', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip', '-j', 'link'], text=True))} == {'lo'}
    baseline = WifiFieldsOriginal(args.manager, args.mib).read_local()['List']
    host = bytes.fromhex('020304050607')
    addresses = [bytes.fromhex('020000000001'), bytes.fromhex('020000000002')]
    master = bytes.fromhex('0110ec000001')
    group = bytes.fromhex('0110ec000002')
    configuration = {'act': 'config', 'SSIDIndex': '1', 'SSID': 'do-not-print-ssid', 'ENCRYPT': 4,
                     'Enable': 1, 'PWD': 'do-not-print-password', 'SSIDAdvertisementEnabled': 'true', 'Standard': '11bgn'}
    policy = {'Peers': [{'MAC': mac.hex(':'), 'Registration': {'OUI': '020304', 'SN': 'synthetic-host',
                        'Ready': 1, 'ExpectedSN': f'synthetic-peer-{i}', 'ExpectedGPONSN': f'ABCD{i:08d}'},
                        'WiFi': [configuration]} for i, mac in enumerate(addresses)]}
    with tempfile.TemporaryDirectory() as name:
        directory = Path(name)
        src = ROOT / 'port/packages/tyfza1-fttr/src'
        binary = directory / 'fttr-sync'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        *[str(src / n) for n in ('fttr-sync.c', 'sync-client.c', 'sync-controller.c', 'sync-wire.c',
                          'sync-peer.c', 'sync-register.c', 'sync-wifi.c', 'sync-json.c', 'omci-socket.c', 'omci-wire.c')],
                        '-ljson-c', '-o', str(binary)], check=True)
        subprocess.run(['ip', 'link', 'add', 'resident-host', 'type', 'veth', 'peer', 'name', 'resident-peer'], check=True)
        for interface, mac in (('resident-host', host), ('resident-peer', addresses[0])):
            subprocess.run(['ip', 'link', 'set', interface, 'address', mac.hex(':'), 'mtu', '1982', 'up'], check=True)
        remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x88aa))
        remote.bind(('resident-peer', 0x88aa))
        remote.setblocking(False)
        config = directory / 'policy.json'
        cases = []

        def send(i, value, fragmented=False, interleave=False, source=None):
            body = json.dumps(value, separators=(',', ':')).encode()
            body = len(body).to_bytes(4, 'big') + body
            prefix = master + (source or addresses[i]) + b'\x88\xaa\xf6'
            if not fragmented:
                remote.send(prefix + b'\0' + body)
            else:
                size = (len(body) + 2) // 3
                parts = [body[j:j + size] for j in range(0, len(body), size)]
                for j in reversed(range(len(parts))):
                    remote.send(prefix + bytes([(len(parts) - 1) << 5 | j << 2 | 1]) + parts[j])
                    if interleave and j == 1:
                        send(i, {'Result': 1})

        def register(i, wrong=False, fragmented=False):
            send(i, {'RPCMethod': 'Register', 'SN': 'replacement' if wrong else f'synthetic-peer-{i}',
                     'GPONSN': f'ABCD{i:08d}', 'LOID': 'do-not-print-loid'}, fragmented)

        def report(i, rows, fragmented=False):
            send(i, {'RPCMethod': 'SetAllSetting', 'Flag': 1, 'STATUS': 0,
                     'content': {'content': {'List': rows}}}, fragmented, fragmented)

        def run(case):
            desired = copy.deepcopy(policy)
            if case == 'ready-zero':
                for peer in desired['Peers']: peer['Registration']['Ready'] = 0
            if case == 'read-only':
                for peer in desired['Peers']: del peer['WiFi']
            if case == 'multiple-rows':
                for peer in desired['Peers']: peer['WiFi'].append(dict(configuration, SSIDIndex='2'))
            if case == 'invalid-duplicate-mac': desired['Peers'][1]['MAC'] = desired['Peers'][0]['MAC']
            if case == 'invalid-duplicate-index': desired['Peers'][0]['WiFi'].append(configuration)
            if case == 'invalid-ready': desired['Peers'][0]['Registration']['Ready'] = 3
            if case == 'invalid-pin': del desired['Peers'][0]['Registration']['ExpectedSN']
            if case == 'invalid-extra': desired['Peers'][0]['Other'] = 1
            if case == 'invalid-root': desired['Other'] = 1
            if case == 'invalid-key': desired['Peers'][0]['WiFi'][0]['PWD'] = 'short'
            config.write_text(json.dumps(desired))
            config.chmod(0o644 if case == 'invalid-permissions' else 0o600)
            if case == 'invalid-large': config.write_text(' ' * 131072)
            if case == 'sixteen-policies':
                for j in range(2, 16):
                    extra = copy.deepcopy(desired['Peers'][0])
                    extra['MAC'] = bytes([2, 0, 0, 0, 0, j + 1]).hex(':')
                    desired['Peers'].append(extra)
                config.write_text(json.dumps(desired))
                assert config.stat().st_size > 1536
            while select.select([remote], [], [], 0)[0]: remote.recv(4096)
            with (directory / 'output').open('w+') as output, (directory / 'error').open('w+') as error:
                proc = subprocess.Popen([str(binary), 'resident-host', 'serve', '0x88aa', str(config)],
                                        stdout=output, stderr=error)
                begin = time.monotonic()
                limit = 7.3 if case in ('offline', 'offline-readback', 'slot-reuse', 'no-replay') else 4.3 if case in (
                    'write-timeout', 'preflight-timeout', 'readback-timeout', 'slow-peer', 'fragment-abandon') else 0.8
                beats = []
                gets = [0, 0]; writes = [0, 0]; replies = [0, 0]
                state = [copy.deepcopy(baseline[:2]), copy.deepcopy(baseline[:2])]
                injected = False
                replacement = bytes.fromhex('020000000099')
                try:
                    while time.monotonic() - begin < limit and proc.poll() is None:
                        readable, _, _ = select.select([remote], [], [], 0.01)
                        if not readable: continue
                        frame, address = remote.recvfrom(4096)
                        if address[2] == socket.PACKET_OUTGOING: continue
                        assert frame[:12] == group + host, (case, frame.hex())
                        if frame[14] & 15 == 1:
                            beats.append(time.monotonic() - begin)
                            for i, mac in enumerate(addresses):
                                if case in ('offline', 'offline-readback', 'slot-reuse') and i == 0 and len(beats) > 1:
                                    if case == 'offline-readback' and len(beats) == 2: report(i, state[i])
                                    continue
                                remote.send(master + mac + b'\x88\xaa\x01\0')
                                if len(beats) == 1:
                                    # Report before Register must never authorize a write.
                                    report(i, state[i])
                                    register(i, case == 'wrong-identity' and i == 0, case == 'fragmented')
                                elif case == 'no-replay': register(i)
                            if case == 'slot-reuse' and len(beats) >= 4:
                                remote.send(master + replacement + b'\x88\xaa\x01\0')
                                send(0, {'RPCMethod': 'Register', 'SN': 'synthetic-peer-0', 'GPONSN': 'ABCD00000000'}, source=replacement)
                                report(0, state[0])
                            if case == 'lease' and len(beats) == 1:
                                locked = subprocess.run([str(binary), 'resident-host', 'discover', '0x88aa'], capture_output=True, text=True)
                                assert locked.returncode == 1 and str(-errno.EADDRINUSE) in locked.stderr
                            continue
                        value = json.loads(frame[20:20 + int.from_bytes(frame[16:20], 'big')])
                        if frame[14] & 15 == 7:
                            i = frame[14] >> 4
                            assert value == {'Result': 1, 'MAC': host.hex().upper(), 'OUI': '020304',
                                             'SN': 'synthetic-host', 'Ready': desired['Peers'][i]['Registration']['Ready']}
                            replies[i] += 1
                            continue
                        i = addresses.index(bytes.fromhex(value['DMAC']))
                        assert frame[14] >> 4 == i and value['RPCMethod'] == 'SetAllSetting' and value['Flag'] == 1
                        content = value['content']
                        if content['act'] == 'get':
                            gets[i] += 1
                            if i == 0 and case == 'offline-readback': continue
                            if i == 0 and case in ('preflight-timeout', 'slow-peer'): continue
                            if i == 0 and case == 'readback-timeout' and gets[i] == 2: continue
                            if i == 0 and case in ('wrong-preflight', 'reregister-preflight') and not injected:
                                injected = True
                                register(i, case == 'wrong-preflight')
                                report(i, state[i])
                                continue
                            if i == 0 and case in ('wrong-readback', 'reregister-readback') and gets[i] == 2:
                                register(i, case == 'wrong-readback')
                                report(i, state[i])
                                continue
                            if i == 0 and case in ('fragment-register', 'fragment-abandon') and not injected:
                                injected = True
                                value = {'RPCMethod': 'Register', 'SN': 'replacement', 'GPONSN': 'ABCD00000000'}
                                body = json.dumps(value).encode(); body = len(body).to_bytes(4, 'big') + body
                                remote.send(master + addresses[i] + b'\x88\xaa\xf7\x21' + body[:30])
                                report(i, state[i])
                                if case == 'fragment-register':
                                    remote.send(master + addresses[i] + b'\x88\xaa\xf7\x25' + body[30:])
                                continue
                            rows = copy.deepcopy(state[i])
                            if i == 0 and case == 'invalid-observed': rows.append(rows[0])
                            if i == 0 and case == 'mismatch' and gets[i] == 2: rows[0]['SSID'] = 'different'
                            report(i, rows, case == 'fragmented')
                        else:
                            writes[i] += 1
                            assert content == desired['Peers'][i]['WiFi'][writes[i] - 1], case
                            if i == 0 and case in ('wrong-write', 'reregister-write'):
                                register(i, case == 'wrong-write')
                                send(i, {'Result': 1})
                                continue
                            row = state[i][int(content['SSIDIndex']) - 1]
                            if not (i == 0 and case == 'nack'):
                                row.update({k: content[k] for k in ('SSID', 'Standard', 'SSIDAdvertisementEnabled')})
                                row['Enable'] = str(content['Enable']); row['ENCRYPT'] = '4'
                            if i == 0 and case in ('write-timeout', 'stop-write', 'offline-readback'): continue
                            send(i, {'Result': 0 if i == 0 and case == 'nack' else 1})
                    if proc.poll() is None: proc.terminate()
                    proc.wait(timeout=2)
                finally:
                    if proc.poll() is None: proc.kill(); proc.wait()
                output.seek(0); text = output.read(); error.seek(0); errors = error.read()
            assert 'do-not-print' not in text + errors
            events = [json.loads(line) for line in text.splitlines()]
            if case.startswith('invalid-') and case != 'invalid-observed':
                assert proc.returncode == 1 and not beats and not events, (case, errors, events)
            else:
                assert proc.returncode == 0, (case, errors)
                for e in events:
                    assert not e['identity_authenticated'] and not e['credentials_verified'] and not e['configuration_verified']
                for i in (0, 1):
                    own = [e for e in events if e['peer'] == addresses[i].hex(':')]
                    applied = [e for e in own if e['event'] == 'sync_resident_apply']
                    no_write = case in ('ready-zero', 'read-only') or i == 0 and case in (
                        'wrong-identity', 'wrong-preflight', 'preflight-timeout', 'slow-peer',
                        'invalid-observed', 'fragment-register', 'fragment-abandon')
                    assert writes[i] == (0 if no_write else 2 if case == 'multiple-rows' else 1), (case, gets, writes, events)
                    if not no_write and not (i == 0 and case in ('wrong-write', 'reregister-write', 'wrong-readback', 'reregister-readback', 'stop-write', 'offline-readback')):
                        assert len(applied) == writes[i], (case, events)
                        expected = -errno.EREMOTEIO if i == 0 and case == 'nack' else -errno.ETIMEDOUT if i == 0 and case in (
                            'write-timeout', 'readback-timeout') else -errno.EUCLEAN if i == 0 and case == 'mismatch' else 0
                        assert applied[-1]['result'] == expected, (case, applied)
                    if i == 0 and case in ('wrong-write', 'reregister-write', 'wrong-readback', 'reregister-readback', 'stop-write', 'offline-readback'):
                        assert not applied and any(e['event'] == 'sync_resident_cancel' for e in own), (case, own)
                    if i == 0 and case in ('offline', 'offline-readback', 'slot-reuse'):
                        assert any(e['event'] == 'sync_resident_offline' for e in own)
                if limit > 4:
                    assert len(beats) >= 3 and all(1.7 < b - a < 2.3 for a, b in zip(beats, beats[1:])), (case, beats)
                if case == 'no-replay': assert all(n > 1 for n in replies) and gets == [2, 2]
            cases.append({'case': case, 'writes': writes, 'gets': gets, 'heartbeats': len(beats), 'events': len(events)})

        for case in ('normal', 'multiple-rows', 'sixteen-policies', 'fragmented', 'lease', 'stop-write', 'ready-zero', 'read-only', 'wrong-identity',
                     'wrong-preflight', 'reregister-preflight', 'wrong-write', 'reregister-write',
                     'wrong-readback', 'reregister-readback', 'fragment-register', 'fragment-abandon',
                     'invalid-observed', 'mismatch', 'nack', 'write-timeout', 'readback-timeout',
                     'preflight-timeout', 'slow-peer', 'no-replay', 'offline', 'offline-readback', 'slot-reuse',
                     'invalid-duplicate-mac', 'invalid-duplicate-index', 'invalid-ready', 'invalid-pin',
                     'invalid-extra', 'invalid-root', 'invalid-key', 'invalid-permissions', 'invalid-large'):
            run(case)
        remote.close()
    result = {'cases': cases, 'scope': 'Native resident AF_PACKET/veth state machine with modeled peers and original local Wi-Fi read rows; no physical slave, optical, password installation or cryptographic identity acceptance.'}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
