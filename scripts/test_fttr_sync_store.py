#!/usr/bin/env python3
"""Crash/restart native syncserv across durable write intent and packet transmission."""
import argparse
import copy
import errno
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from trace_stock_fttr_wifi_fields import WifiFieldsOriginal

FAULTS = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
int fsync(int fd) {
    static int dirs;
    struct stat st;
    const char *mode = getenv("FTTR_TEST_SYNC_FAULT");
    int (*original)(int) = dlsym(RTLD_NEXT, "fsync");
    if (fstat(fd, &st)) return -1;
    int directory = S_ISDIR(st.st_mode);
    if (directory) dirs++;
    if (mode && ((!strcmp(mode, "fail-file") && !directory) ||
                 (!strcmp(mode, "fail-commit") && directory && dirs > 1))) {
        errno = EIO; return -1;
    }
    int result = original(fd);
    if (mode && !result && ((!strcmp(mode, "stop-file") && !directory) ||
                           (!strcmp(mode, "stop-commit") && directory && dirs > 1)))
        raise(SIGSTOP);
    return result;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager', type=Path, required=True)
    parser.add_argument('--mib', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip', '-j', 'link'], text=True))} == {'lo'}
    baseline = WifiFieldsOriginal(args.manager, args.mib).read_local()['List'][:2]
    host = bytes.fromhex('020304050607'); mac = bytes.fromhex('020000000001')
    master = bytes.fromhex('0110ec000001'); group = bytes.fromhex('0110ec000002')
    desired = {'act': 'config', 'SSIDIndex': '1', 'SSID': 'do-not-print-ssid', 'ENCRYPT': 4,
               'Enable': 1, 'PWD': 'do-not-print-password', 'SSIDAdvertisementEnabled': 'true', 'Standard': '11bgn'}
    policy = {'Peers': [{'MAC': mac.hex(':'), 'Revision': 'setup-1', 'Registration': {'OUI': '020304',
                         'SN': 'synthetic-host', 'Ready': 1, 'ExpectedSN': 'synthetic-peer'},
                         'WiFi': [desired, dict(desired, SSIDIndex='2')]}]}
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name); src = ROOT / 'port/packages/tyfza1-fttr/src'
        binary = folder / 'fttr-sync'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        *[str(src / n) for n in ('fttr-sync.c', 'sync-client.c', 'sync-controller.c', 'sync-store.c',
                          'sync-wire.c', 'sync-peer.c', 'sync-register.c', 'sync-wifi.c', 'sync-json.c', 'omci-socket.c', 'omci-wire.c')],
                        '-ljson-c', '-o', str(binary)], check=True)
        fault = folder / 'fault.c'; fault.write_text(FAULTS)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', str(fault), '-ldl', '-o', str(folder / 'fault.so')], check=True)
        subprocess.run(['ip', 'link', 'add', 'store-host', 'type', 'veth', 'peer', 'name', 'store-peer'], check=True)
        for interface, address in (('store-host', host), ('store-peer', mac)):
            subprocess.run(['ip', 'link', 'set', interface, 'address', address.hex(':'), 'mtu', '1982', 'up'], check=True)
        remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x88aa))
        remote.bind(('store-peer', 0x88aa)); remote.setblocking(False)
        cases = []

        def send(value):
            text = json.dumps(value, separators=(',', ':')).encode()
            remote.send(master + mac + b'\x88\xaa\xf6\0' + len(text).to_bytes(4, 'big') + text)

        def run(label, store, state, data=None, behavior='', fault_mode='', expected_writes=0, expected_error=None):
            config = folder / 'policy.json'; config.write_text(json.dumps(data or policy)); config.chmod(0o600)
            while select.select([remote], [], [], 0)[0]: remote.recv(4096)
            env = dict(os.environ)
            if fault_mode: env.update(LD_PRELOAD=str(folder / 'fault.so'), FTTR_TEST_SYNC_FAULT=fault_mode)
            with (folder / 'out').open('w+') as output, (folder / 'err').open('w+') as errors:
                proc = subprocess.Popen([str(binary), 'store-host', 'serve', '0x88aa', str(config), '--state', str(store)],
                                        env=env, stdout=output, stderr=errors)
                deadline = time.monotonic() + (3.5 if behavior == 'timeout-recovery' else 0.7)
                writes = []; gets = 0; beats = 0; stopped = False
                try:
                    while proc.poll() is None and time.monotonic() < deadline:
                        if fault_mode.startswith('stop-') and not stopped:
                            status = Path(f'/proc/{proc.pid}/status').read_text()
                            if '\nState:\tT' in status:
                                stopped = True
                                assert not writes
                                if behavior == 'replace-before-send':
                                    send({'RPCMethod': 'Register', 'SN': 'different-peer'})
                                    proc.send_signal(signal.SIGCONT)
                                else:
                                    proc.kill(); break
                        if not select.select([remote], [], [], 0.01)[0]: continue
                        frame, address = remote.recvfrom(4096)
                        if address[2] == socket.PACKET_OUTGOING: continue
                        assert frame[:12] == group + host
                        if frame[14] & 15 == 1:
                            beats += 1
                            remote.send(master + mac + b'\x88\xaa\x01\0')
                            if beats == 1:
                                send({'RPCMethod': 'Register', 'SN': 'different-peer' if behavior == 'wrong-registration' else 'synthetic-peer'})
                            continue
                        if frame[14] & 15 == 7: continue
                        request = json.loads(frame[20:]); content = request['content']
                        if content['act'] == 'get':
                            gets += 1
                            if behavior == 'timeout-recovery': continue
                            send({'RPCMethod': 'SetAllSetting', 'Flag': 1, 'STATUS': 0, 'content': {'content': {'List': state}}})
                        else:
                            index = int(content['SSIDIndex'])
                            # Observe the committed intent before acknowledging the write.
                            revision = (data or policy)['Peers'][0]['Revision']
                            record = json.loads((store / (mac.hex() + '-' + revision + '.json')).read_text())
                            assert record['attempted'] & (1 << (index - 1))
                            assert record['wifi'][index - 1] == content
                            assert (store / (mac.hex() + '-' + revision + '.json')).stat().st_mode & 0o777 == 0o600
                            writes.append(index)
                            if behavior != 'crash-no-apply':
                                row = state[index - 1]
                                row.update({k: content[k] for k in ('SSID', 'SSIDAdvertisementEnabled', 'Standard')})
                                row['Enable'] = str(content['Enable']); row['ENCRYPT'] = '4'
                            if behavior in ('crash-applied', 'crash-no-apply'):
                                proc.kill(); break
                            send({'Result': 1})
                    if proc.poll() is None: proc.terminate()
                    proc.wait(timeout=2)
                finally:
                    if proc.poll() is None: proc.kill(); proc.wait()
                output.seek(0); text = output.read(); errors.seek(0); error = errors.read()
            assert len(writes) == expected_writes, (label, writes, error, text)
            assert 'do-not-print' not in text + error
            events = [json.loads(line) for line in text.splitlines()]
            if expected_error:
                assert proc.returncode == 1 and str(-expected_error) in error and not beats, (label, error, beats)
            elif behavior.startswith('crash-') or (fault_mode.startswith('stop-') and behavior != 'replace-before-send'):
                assert proc.returncode == -signal.SIGKILL, (label, proc.returncode)
            elif fault_mode.startswith('fail-'):
                assert proc.returncode == 1 and str(-errno.EIO) in error
            else:
                assert proc.returncode == 0, (label, error)
            recovered = [event for event in events if event['event'] == 'sync_resident_recovery']
            assert all(not e['configuration_verified'] and not e['credentials_verified'] for e in events)
            assert all(e['prior_write_outcome_unknown'] and not e['write_replayed'] for e in recovered)
            cases.append({'case': label, 'writes': writes, 'gets': gets, 'recoveries': len(recovered), 'exit': proc.returncode})
            return recovered

        def directory(label):
            path = folder / label; path.mkdir(mode=0o700)
            return path

        store = directory('normal'); state = copy.deepcopy(baseline)
        run('fresh', store, state, expected_writes=2)
        recovered = run('restart-completed', store, state)
        assert len(recovered) == 2 and all(e['readable_fields_match'] for e in recovered)
        changed = copy.deepcopy(policy); changed['Peers'][0]['WiFi'][0]['PWD'] = 'do-not-print-changed'
        run('same-revision-new-password', store, state, changed, expected_error=errno.ESTALE)
        changed = copy.deepcopy(policy); changed['Peers'][0]['Registration']['ExpectedSN'] = 'replacement'
        run('same-revision-new-pin', store, state, changed, expected_error=errno.ESTALE)
        changed = copy.deepcopy(policy); changed['Peers'][0]['WiFi'].reverse()
        run('same-revision-reordered-rows', store, state, changed, expected_error=errno.ESTALE)
        changed = copy.deepcopy(policy); changed['Peers'][0]['Registration']['Ready'] = 0
        assert len(run('ready-change-read-only', store, state, changed)) == 2
        changed = copy.deepcopy(policy); changed['Peers'][0]['Revision'] = 'setup-2'; changed['Peers'][0]['WiFi'][0]['PWD'] = 'do-not-print-changed'
        run('explicit-new-revision', store, state, changed, expected_writes=2)
        assert len(run('return-to-old-revision', store, state)) == 2
        run('wrong-registered-identity', store, state, behavior='wrong-registration')
        run('recovery-timeout', store, state, behavior='timeout-recovery')
        subprocess.run(['ip', 'link', 'set', 'store-host', 'address', '02:03:04:05:06:08'], check=True)
        run('different-master-mac', store, state, expected_error=errno.ESTALE)
        subprocess.run(['ip', 'link', 'set', 'store-host', 'address', host.hex(':')], check=True)
        for behavior in ('crash-applied', 'crash-no-apply'):
            store = directory(behavior); state = copy.deepcopy(baseline)
            run(behavior, store, state, behavior=behavior, expected_writes=1)
            recovered = run(behavior + '-restart', store, state, expected_writes=1 if behavior == 'crash-applied' else 0)
            assert len(recovered) == 1 and recovered[0]['readable_fields_match'] == (behavior == 'crash-applied')
        for mode in ('stop-file', 'stop-commit', 'fail-file', 'fail-commit'):
            store = directory(mode); state = copy.deepcopy(baseline)
            run(mode, store, state, fault_mode=mode)
            committed = list(store.glob('*.json'))
            assert bool(committed) == (mode in ('stop-commit', 'fail-commit'))
            run(mode + '-restart', store, state, expected_writes=0 if committed else 2)
        store = directory('replace'); state = copy.deepcopy(baseline)
        run('replacement-after-fsync', store, state, behavior='replace-before-send', fault_mode='stop-commit')
        run('replacement-after-fsync-restart', store, state)
        store = directory('bad-record'); state = copy.deepcopy(baseline)
        record = store / (mac.hex() + '-setup-1.json')
        record.write_text('{'); record.chmod(0o600)
        run('corrupt-record', store, state, expected_error=errno.EINVAL)
        record.unlink(); record.symlink_to(folder / 'absent')
        run('symlink-record', store, state, expected_error=errno.ELOOP)
        record.unlink()
        run('prepare-record', store, state, expected_writes=2)
        record.chmod(0o644)
        run('public-record', store, state, expected_error=errno.EACCES)
        record.chmod(0o600)
        saved = record.read_text()
        data = json.loads(saved); data['peer'] = '020000000099'; record.write_text(json.dumps(data))
        run('record-from-other-peer', store, state, expected_error=errno.ESTALE)
        record.write_text(saved)
        data = json.loads(record.read_text()); data['attempted'] = 65536; record.write_text(json.dumps(data))
        run('invalid-attempt-mask', store, state, expected_error=errno.EBADMSG)
        store = directory('permissions'); store.chmod(0o755)
        run('public-directory', store, state, expected_error=errno.EACCES)
        store.chmod(0o700)
        descriptor = os.open(store, os.O_RDONLY | os.O_DIRECTORY)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        run('directory-lease', store, state, expected_error=errno.EWOULDBLOCK)
        os.close(descriptor)
        changed = copy.deepcopy(policy); del changed['Peers'][0]['Revision']
        run('missing-revision', store, state, changed, expected_error=errno.EINVAL)
        for revision in ('../escape', '', 'x' * 33, 'bad\0value'):
            changed['Peers'][0]['Revision'] = revision
            run('invalid-revision-' + str(len(revision)), store, state, changed, expected_error=errno.EINVAL)
        remote.close()
    result = {'cases': cases, 'scope': 'Native AF_PACKET/veth with modeled peer, real fsync/filesystem recovery and explicit libc fsync fault/stop hooks. Tests SIGKILL/restart and ordering; not a physical power-loss, flash-controller or slave credential acceptance test.'}
    args.output.write_text(json.dumps(result, indent=2) + '\n'); print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
