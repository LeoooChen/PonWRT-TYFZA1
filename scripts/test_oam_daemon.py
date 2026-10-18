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
parser.add_argument('--openwrt', type=Path, default=root / 'sources/openwrt')
args = parser.parse_args()
toolchain = args.openwrt / 'staging_dir/toolchain-aarch64_cortex-a55_gcc-14.4.0_musl'
libraries = args.openwrt / 'staging_dir/target-aarch64_cortex-a55_musl/usr/lib'
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
    mpcp.write_text('configured=1 enabled=1 registered=00000000 epochs=0000000000000001\n')
    state = temporary / 'state'
    state.mkdir()
    state_file = state / 'llid-0.json'
    for command in (['ip', 'link', 'set', 'lo', 'up'],
                    ['ip', 'link', 'add', 'pon-oam0', 'type', 'veth', 'peer', 'name', 'olt'],
                    ['ip', 'link', 'set', 'pon-oam0', 'address', '02:11:22:33:44:55'],
                    ['ip', 'link', 'set', 'olt', 'address', '02:10:20:30:40:50'],
                    ['ip', 'link', 'set', 'pon-oam0', 'up'], ['ip', 'link', 'set', 'olt', 'up'],
                    ['ip', 'link', 'add', 'lan4', 'type', 'veth', 'peer', 'name', 'uni-peer']):
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
        mpcp.write_text('configured=1 enabled=1 registered=00000001 epochs=0000000000000001\n')
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
        assert auth[26:50] == b'0012345678'.rjust(24, b'\0')
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
        assert versions[48:64] == b'SOFTWARE-2'.rjust(16, b'\0')
        release = (root / 'VERSION').read_text().strip().split('-')[0]
        assert versions[83:87] == bytes([3, *map(int, release.split('.'))])
        tests.append('ONU SN carries UCI software identity; FirmwareVer carries three release octets')
        uni_object = bytes.fromhex('37000104 01000001')
        for command in (['ip', 'link', 'set', 'lan4', 'up'], ['ip', 'link', 'set', 'uni-peer', 'up']):
            subprocess.run(command, check=True)
        before_flags = json.loads(subprocess.check_output(['ip', '-j', 'link', 'show', 'lan4']))[0]['flags']
        tx_control = temporary / 'epon_tx_control'
        tx_control.write_bytes(b'local-owner')
        blocked = [(0xc9, 1, None), (9, 5, struct.pack('!I', 1)),
                   (9, 0xb, None), (9, 0xc, struct.pack('!I', 1)), (0xc7, 0x12, b'\1'),
                   (0xc7, 0xa1, b'\0\2' + bytes.fromhex('021122334455') + bytes(4)),
                   (0xc7, 0x81, struct.pack('!HI', 0x101, 2)),
                   (0xc7, 0x82, struct.pack('!HII', 0x101, 100, 90)),
                   (0xc7, 0xb1, struct.pack('!HI', 2, 900)), (0xc7, 0xb2, b'\0')]
        for branch, leaf, data in blocked:
            variable = bytes([branch]) + struct.pack('!H', leaf)
            payload = b'\x11\x11\x11\x03' + uni_object + variable
            if data is not None:
                payload += bytes([len(data)]) + data
            sock.send(ethernet(payload + b'\0', code=0xfe, flags=0x50))
            assert receive(sock, 0xfe)[30:34] == variable + b'\x87', (branch, leaf)
        assert tx_control.read_bytes() == b'local-owner'
        assert json.loads(subprocess.check_output(['ip', '-j', 'link', 'show', 'lan4']))[0]['flags'] == before_flags
        tests.append('remote reboot/TX/LAN/alarm/PM Set requests are refused without changing local port or TX controls')

        fec_file = temporary / 'fec'
        fec_file.write_text('supported=1 upstream=1\n')
        sock.send(ethernet(b'\x11\x11\x11\x01' + uni_object + bytes.fromhex('c70082020101 070139 00'), code=0xfe, flags=0x50))
        assert receive(sock, 0xfe)[30:42] == bytes.fromhex('c7008286 0701390400000002')
        for leaf in (0x81, 0xb1, 0xb2, 0xb3):
            variable = bytes([0xc7, 0, leaf])
            sock.send(ethernet(b'\x11\x11\x11\x01' + uni_object + variable + b'\0', code=0xfe, flags=0x50))
            assert receive(sock, 0xfe)[30:34] == variable + b'\x86'
        tests.append('removed alarm/PM Gets report unsupported; parameterized Get still preserves the following FEC query')

        (temporary / 'llid_count').write_text('1\n')
        sock.send(ethernet(b'\x11\x11\x11\x01\xc7\0\x0c\0', code=0xfe, flags=0x50))
        assert receive(sock, 0xfe)[22:29] == bytes.fromhex('c7000c03010001')
        (temporary / 'transceiver').write_text('temperature_raw=-256 voltage_raw=33000 bias_raw=1234 tx_power_raw=5678 rx_power_raw=65535\n')
        sock.send(ethernet(b'\x11\x11\x11\x01\xc7\0\x05\0', code=0xfe, flags=0x50))
        assert receive(sock, 0xfe)[22:36] == b'\xc7\0\x05\x0a' + struct.pack('!5H', 0xff00, 33000, 1234, 5678, 65535)
        sock.send(ethernet(b'\x11\x11\x11\x01' + uni_object + b'\x07\0\x25\0', code=0xfe, flags=0x50))
        assert receive(sock, 0xfe)[30:38] == bytes.fromhex('0700250400000002')
        tests.append('read-only port state and optical diagnosis remain usable; ONU no longer advertises OAM TX power control')

        # Also cover the alternate alarm opcode and the old delayed-reboot deadline.
        # check-pon-userspace.sh isolates PID1 to contain any future regression.
        sock.send(ethernet(b'\x11\x11\x11\xff\x02\0\1' + struct.pack('!HIHI', 4, 0, 0x101, 1), code=0xfe, flags=0x50))
        until = time.monotonic() + 3.4
        while time.monotonic() < until:
            if select.select([sock], [], [], .2)[0]:
                frame = sock.recv(2048)
                assert frame[17] not in (1, 0xfe), 'unexpected alarm/management reply'
                if frame[17] == 0:
                    sock.send(ethernet(peer_info, flags=16))
        retained = status(state_file, lambda s: s['auth_state'] == 'accepted')
        assert process.poll() is None and retained['generation'] == accepted['generation']
        assert 'alarm_error' not in retained and 'pm_error' not in retained
        tests.append('no proactive alarm events or delayed reboot; registration/auth stay live after refused management traffic')
        set_request = bytes.fromhex('11111103 37000104 01000007 c700250102 3600010103 c700130200e3 00')
        set_response = bytes.fromhex('11111104 37000104 01000007 c7002587 3600010103 c7001387 00')
        sock.send(ethernet(set_request, code=0xfe, flags=0x50))
        actual_set = receive(sock, 0xfe)
        assert actual_set[18:18 + len(set_response)] == set_response
        tests.append('Set response retains complete four-byte and one-byte port instances')
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
        mpcp.write_text('configured=1 enabled=1 registered=00000000 epochs=0000000000000001\n')
        reset = status(state_file, lambda s: not s['registered'])
        assert reset['auth_state'] == 'waiting' and reset['auth_results'] == 2
        assert not reset['discovery_stable']
        tests.append('MPCP loss resets current auth and retains result history')
        mpcp.write_text('configured=1 enabled=1 registered=00000001 epochs=0000000000000001\n')
        fresh = receive(sock, 0)
        assert fresh[20:34] != local
        fresh_info = b'\x01\x10' + peer_local + b'\x02\x10' + fresh[20:34] + b'\xfe\x07\x11\x11\x11\x01\x30\0\0'
        sock.send(ethernet(fresh_info, flags=16))
        status(state_file, lambda s: s['discovery_stable'])
        sock.send(ethernet(b'\x11\x11\x11\x05\x03\0\0', code=0xfe, flags=0x50))
        status(state_file, lambda s: s['auth_state'] == 'accepted')
        before_epoch = status(state_file, lambda s: s['discovery_stable'])
        mpcp.write_text('configured=1 enabled=1 registered=00000001 epochs=0000000000000002\n')
        changed_epoch = status(state_file, lambda s: s['generation'] == before_epoch['generation'] + 1)
        assert changed_epoch['registered'] and not changed_epoch['discovery_stable']
        assert changed_epoch['auth_state'] == 'waiting'
        fresh = receive(sock, 0)
        while int.from_bytes(fresh[21:23], 'big') != (changed_epoch['generation'] & 0xffff):
            fresh = receive(sock, 0)
        fresh_info = b'\x01\x10' + peer_local + b'\x02\x10' + fresh[20:34] + b'\0\0'
        sock.send(ethernet(fresh_info, flags=16))
        status(state_file, lambda s: s['discovery_stable'])
        sock.send(ethernet(b'\x11\x11\x11\x05\x03\0\0', code=0xfe, flags=0x50))
        status(state_file, lambda s: s['auth_state'] == 'accepted')
        tests.append('a registration epoch change invalidates auth even when the sampled bitmap stays registered')
        timed_out = status(state_file, lambda s: not s['discovery_stable'] and s['auth_state'] == 'waiting', timeout=7)
        assert timed_out['auth_successes'] == 3 and timed_out['registered']
        tests.append('OAM peer timeout revokes current auth while MPCP remains registered')
        # Establish another accepted session, then keep feeding invalid or
        # unhandled traffic past the five-second liveness deadline.
        fresh = receive(sock, 0)
        while int.from_bytes(fresh[21:23], 'big') != (timed_out['generation'] & 0xffff):
            fresh = receive(sock, 0)
        fresh_info = b'\x01\x10' + peer_local + b'\x02\x10' + fresh[20:34] + b'\0\0'
        sock.send(ethernet(fresh_info, flags=16))
        status(state_file, lambda s: s['discovery_stable'])
        sock.send(ethernet(b'\x11\x11\x11\x05\x03\0\0', code=0xfe, flags=0x50))
        live = status(state_file, lambda s: s['auth_state'] == 'accepted')
        invalid = (ethernet(b'', code=0xfe)[:18], ethernet(b'', code=2),
                   ethernet(b'\x12\x34\x56\x05\x03\0\0', code=0xfe),
                   ethernet(b'\x11\x11\x11\x7f', code=0xfe))
        for _ in range(10):
            for frame in invalid:
                sock.send(frame)
            time.sleep(.7)
        expired = status(state_file, lambda s: s['auth_state'] == 'waiting' and not s['discovery_stable'])
        assert expired['registered'] and expired['generation'] == live['generation'] + 1
        assert expired['auth_successes'] == 4
        tests.append('truncated, unsupported-code and unknown-OUI traffic cannot extend an accepted session')
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
