#!/usr/bin/env python3
"""Exercise the actual ARM64 musl OAM process in an isolated Ubuntu network namespace."""
import argparse
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=Path, default=root / 'artifacts/oam-control/pon-oamd')
args = parser.parse_args()
toolchain = root / 'sources/openwrt/staging_dir/toolchain-aarch64_cortex-a55_gcc-14.4.0_musl'
libraries = root / 'sources/openwrt/staging_dir/target-aarch64_cortex-a55_musl/usr/lib'
loader = toolchain / 'lib/libc.so'
peer_mac = bytes.fromhex('021020304050')
tests = []


def ethernet(payload, code=0, flags=0):
    frame = bytes.fromhex('0180c2000002') + peer_mac + b'\x88\x09\x03' + struct.pack('!H', flags) + bytes([code]) + payload
    return frame.ljust(60, b'\0')


def receive(sock, code, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not select.select([sock], [], [], max(0, end - time.monotonic()))[0]:
            break
        frame = sock.recv(2048)
        if len(frame) >= 18 and frame[12:15] == b'\x88\x09\x03' and frame[17] == code:
            return frame
    raise AssertionError(f'No OAM code {code} response')


def status(path, predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            result = json.loads(path.read_text())
            if predicate(result):
                return result
        time.sleep(.05)
    raise AssertionError(f'Status condition missing: {path.read_text() if path.exists() else "absent"}')


with tempfile.TemporaryDirectory(dir=root / 'artifacts') as temporary:
    temporary = Path(temporary)
    config = temporary / 'config'
    config.mkdir()
    (config / 'pon').write_text("config pon 'pon'\n option loid '0012345678'\n option loid_password ''\n option vendor_id 'TEST'\n option oam_model 'FZA1'\n option equipment_id 'TYFZA1'\n option hardware_version 'HARDWARE-1'\n option software_version 'SOFTWARE-2'\n")
    mpcp = temporary / 'mpcp'
    mpcp.write_text('configured=1 enabled=1 registered=00000000\n')
    state = temporary / 'state'
    state.mkdir()
    state_file = state / 'llid-0.json'
    for command in (['ip', 'link', 'set', 'lo', 'up'],
                    ['ip', 'link', 'add', 'pon-oam0', 'type', 'veth', 'peer', 'name', 'olt'],
                    ['ip', 'link', 'set', 'pon-oam0', 'address', '02:11:22:33:44:55'],
                    ['ip', 'link', 'set', 'olt', 'address', '02:10:20:30:40:50'],
                    ['ip', 'link', 'set', 'pon-oam0', 'up'], ['ip', 'link', 'set', 'olt', 'up']):
        subprocess.run(command, check=True)
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x8809))
    sock.bind(('olt', 0))
    process = subprocess.Popen([str(loader), '--library-path', str(libraries) + ':' + str(toolchain / 'lib'),
        str(args.binary), '--config-dir', str(config), '--mpcp-status', str(mpcp), '--state-dir', str(state)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        waiting = status(state_file, lambda s: not s['registered'])
        assert not select.select([sock], [], [], 1.2)[0]
        tests.append('unregistered process sends no OAM')
        mpcp.write_text('configured=1 enabled=1 registered=00000001\n')
        info = receive(sock, 0)
        local = info[20:34]
        peer_local = b'\x01\x00\x07\x06\x14\x05\xee\x00\x0d\xb6\x01\x02\x03\x04'
        peer_info = b'\x01\x10' + peer_local + b'\x02\x10' + local + b'\xfe\x07\x11\x11\x11\x01\x30\0\0'
        sock.send(ethernet(peer_info, flags=16))
        stable = status(state_file, lambda s: s['discovery_stable'])
        assert stable['generation'] != waiting['generation']
        tests.append('registered Information discovery reaches stable with remote echo')
        challenge = b'\x11\x11\x11\x05\x01\x00\x01\x01'
        sock.send(ethernet(challenge, code=0xfe, flags=0x50))
        auth = receive(sock, 0xfe)
        assert auth[18:26] == b'\x11\x11\x11\x05\x02\x00\x25\x01'
        assert auth[26:50] == b'0012345678'.ljust(24, b'\0')
        assert auth[50:62] == bytes(12)
        tests.append('actual process sends 24-byte LOID and blank 12-byte password')
        sock.send(ethernet(b'\x11\x11\x11\x05\x03\0\0', code=0xfe, flags=0x50))
        accepted = status(state_file, lambda s: s['auth_state'] == 'accepted')
        assert accepted['auth_results'] == 1
        assert '0012345678' not in state_file.read_text()
        tests.append('OLT success changes state without publishing credential values')
        sock.send(ethernet(b'\x11\x11\x11\x01\xc7\0\x01\xc7\0\x02\0', code=0xfe, flags=0x50))
        versions = receive(sock, 0xfe)
        assert versions[21] == 2 and versions[26:34] == b'TESTFZA1'
        assert versions[40:48] == b'HARDWARE'
        assert versions[48:64] == b'SOFTWARE-2'.ljust(16, b'\0')
        assert versions[84:100] == b'SOFTWARE-2'.ljust(16, b'\0')
        tests.append('UCI hardware/software versions enter live Get replies')
        # Continuous Information must revoke auth immediately, without waiting
        # for the five-second peer timeout or a changed peer Local TLV.
        sock.send(ethernet(b'\x01\x10' + peer_local + b'\0\0', flags=8))
        regressed = status(state_file, lambda s: not s['discovery_stable'] and s['auth_state'] == 'waiting')
        assert regressed['auth_successes'] == 1 and regressed['auth_results'] == 1
        assert regressed['generation'] == accepted['generation'] + 1
        fresh = receive(sock, 0)
        assert fresh[20:34] != local
        sock.send(ethernet(b'\x01\x10' + peer_local + b'\x02\x10' + fresh[20:34] + b'\0\0', flags=16))
        status(state_file, lambda s: s['discovery_stable'])
        tests.append('same Local EVAL without Remote echo revokes success and negotiates a fresh revision')
        sock.send(ethernet(challenge, code=0xfe, flags=0x50))
        receive(sock, 0xfe)
        status(state_file, lambda s: s['auth_state'] == 'challenge')
        sock.send(ethernet(b'\x11\x11\x11\x05\x04\0\x01\x07', code=0xfe, flags=0x50))
        rejected = status(state_file, lambda s: s['auth_state'] == 'rejected')
        assert rejected['auth_failure'] == 7 and rejected['auth_failures'] == 1
        tests.append('reauthentication clears success and records rejection')
        mpcp.write_text('configured=1 enabled=1 registered=00000000\n')
        reset = status(state_file, lambda s: not s['registered'])
        assert reset['auth_state'] == 'waiting' and reset['auth_results'] == 2
        assert not reset['discovery_stable']
        tests.append('MPCP loss resets current auth and retains result history')
        mpcp.write_text('configured=1 enabled=1 registered=00000001\n')
        fresh = receive(sock, 0)
        assert fresh[20:34] != local
        fresh_info = b'\x01\x10' + peer_local + b'\x02\x10' + fresh[20:34] + b'\xfe\x07\x11\x11\x11\x01\x30\0\0'
        sock.send(ethernet(fresh_info, flags=16))
        status(state_file, lambda s: s['discovery_stable'])
        sock.send(ethernet(b'\x11\x11\x11\x05\x03\0\0', code=0xfe, flags=0x50))
        status(state_file, lambda s: s['auth_state'] == 'accepted')
        timed_out = status(state_file, lambda s: not s['discovery_stable'] and s['auth_state'] == 'waiting', timeout=7)
        assert timed_out['auth_successes'] == 2 and timed_out['registered']
        tests.append('OAM peer timeout revokes current auth while MPCP remains registered')
        duplicate = subprocess.run([str(loader), '--library-path', str(libraries) + ':' + str(toolchain / 'lib'),
            str(args.binary), '--config-dir', str(config), '--mpcp-status', str(mpcp), '--state-dir', str(state)],
            capture_output=True, text=True)
        assert duplicate.returncode != 0
        tests.append('duplicate LLID daemon rejected')
        process.terminate()
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 0, stderr
        assert not state_file.exists()
        tests.append('SIGTERM removes current status and closes socket')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        sock.close()
(root / 'logs/oam-daemon-netns-test.json').write_text(json.dumps({'binary': str(args.binary),
    'runtime':'AArch64 OpenWrt GCC14.4/musl', 'network':'isolated veth namespace',
    'passed': tests, 'PON_hardware_verified': False}, indent=2) + '\n')
print(f'{len(tests)} actual ARM64/musl daemon integration checks passed')
