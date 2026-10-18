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
        pm_file = temporary / 'pm_counters'
        pm_object = bytes.fromhex('37000404 00000000')

        def mib_record(generation, base):
            records = [struct.pack('!38Q', generation, *[base + i for i in range(37)])]
            records += [struct.pack('!38Q', 0, *[2000 + port * 100 + i for i in range(37)]) for port in range(4)]
            pm_file.write_bytes(b''.join(records))

        def pm_get(leaf):
            variable = bytes([0xc7, 0, leaf])
            sock.send(ethernet(b'\x11\x11\x11\x01' + pm_object + variable + b'\0', code=0xfe, flags=0x50))
            frame = receive(sock, 0xfe)
            assert frame[22:30] == pm_object
            position, value = 30, b''
            while frame[position:position + 3] == variable:
                width = frame[position + 3]
                if width & 0x80:
                    return width
                width = width or 128
                value += frame[position + 4:position + 4 + width]
                position += 4 + width
            return value

        def pm_set(leaf, value):
            payload = b'\x11\x11\x11\x03' + pm_object + bytes([0xc7, 0, leaf, len(value)]) + value + b'\0'
            sock.send(ethernet(payload, code=0xfe, flags=0x50))
            assert receive(sock, 0xfe)[30:34] == bytes([0xc7, 0, leaf, 0x80])

        mib_record(1, 1000)
        assert pm_get(0xb2) == struct.pack('!37Q', *range(1000, 1037))
        tests.append('PM Current Get sends all37 hardware counters through three CTC variable segments')
        pm_set(0xb2, b'\0')
        assert pm_get(0xb2) == bytes(296)
        pm_set(0xb1, struct.pack('!HI', 2, 2))
        assert pm_get(0xb1) == struct.pack('!HI', 2, 2)
        mib_record(1, 1017)
        time.sleep(2.1)
        assert pm_get(0xb3) == struct.pack('!37Q', *([17] * 37))
        assert pm_get(0xb2) == bytes(296)
        tests.append('PM enable/period/current reset produces measured period history without clearing lifetime MIB')
        pm_set(0xb1, struct.pack('!HI', 1, 900))
        mib_record(2, 10)
        assert pm_get(0xb2) == bytes(296) and pm_get(0xb3) == bytes(296)
        tests.append('PON MIB reset generation discards obsolete baseline and history rather than underflowing counters')
        pm_file.unlink()
        assert pm_get(0xb2) == 0x87
        status(state_file, lambda s: s['pm_error'] < 0)
        tests.append('failed hardware PM read remains an error response and visible status')
        mib_record(3, 10)
        transceiver = temporary / 'transceiver'

        def sensor(rx):
            transceiver.write_text(f'temperature_raw=256 voltage_raw=33000 bias_raw=1000 tx_power_raw=1000 rx_power_raw={rx}\n')

        sensor(101)
        threshold = b'\x11\x11\x11\x03\xc7\x00\x82\x0a\x01\x01' + struct.pack('!II', 100, 90) + b'\0'
        sock.send(ethernet(threshold, code=0xfe, flags=0x50))
        assert receive(sock, 0xfe)[22:26] == b'\xc7\x00\x82\x80'
        sock.send(ethernet(b'\x11\x11\x11\x01\xc7\x00\x82\x02\x01\x01\0', code=0xfe, flags=0x50))
        thresholds = receive(sock, 0xfe)
        assert thresholds[26:36] == b'\x01\x01' + struct.pack('!II', 100, 90)
        tests.append('AlarmThreshold Get returns distinct configured trigger/clear thresholds')
        alarm_enable = b'\x11\x11\x11\x03\xc7\x00\x81\x06\x01\x01' + struct.pack('!I', 2) + b'\0'
        sock.send(ethernet(alarm_enable, code=0xfe, flags=0x50))
        assert receive(sock, 0xfe)[22:26] == b'\xc7\x00\x81\x80'
        alarm = receive(sock, 1)
        assert alarm[18:22] == b'\0\0\xfe\x14' and alarm[22:25] == b'\x11\x11\x11'
        assert alarm[25:40] == struct.pack('!HIHHBI', 4, 0, 0x101, 0, 0, 101)
        tests.append('enabled optical threshold produces actual code1/CTC alarm notification')
        event_get = b'\x11\x11\x11\xff\x01\x00\x01' + struct.pack('!HIH', 4, 0, 0x101)
        sock.send(ethernet(event_get, code=0xfe, flags=0x50))
        event_status = receive(sock, 0xfe)
        assert event_status[21:25] == b'\xff\x03\x00\x01'
        assert event_status[25:37] == struct.pack('!HIHI', 4, 0, 0x101, 1)
        tests.append('CTC event Status Get returns the configured alarm enable state')
        sensor(95)
        try:
            receive(sock, 1, timeout=1.2)
            raise RuntimeError('alarm cleared inside hysteresis band')
        except AssertionError:
            pass
        sock.send(ethernet(event_get, code=0xfe, flags=0x50))
        receive(sock, 0xfe)
        transceiver.unlink()
        status(state_file, lambda s: s['alarm_error'] < 0)
        try:
            receive(sock, 1, timeout=1.2)
            raise RuntimeError('sensor failure fabricated an alarm event')
        except AssertionError:
            pass
        tests.append('hysteresis retains state; a failed sensor read publishes error without false event')
        sensor(89)
        sock.send(ethernet(event_get, code=0xfe, flags=0x50))
        receive(sock, 0xfe)
        clear = receive(sock, 1)
        assert clear[18:20] == b'\x00\x01'
        assert clear[25:40] == struct.pack('!HIHHBI', 4, 0, 0x101, 0, 1, 89)
        tests.append('crossing clear threshold emits recovery notification with next event sequence')
        alarm_disable = alarm_enable[:-5] + struct.pack('!I', 1) + b'\0'
        sock.send(ethernet(alarm_disable, code=0xfe, flags=0x50))
        assert receive(sock, 0xfe)[22:26] == b'\xc7\x00\x81\x80'
        transceiver.unlink()

        def counter_config(object, id, trigger, clear, enabled):
            payload = b'\x11\x11\x11\x03' + object + b'\xc7\x00\x82\x0a' + struct.pack('!HII', id, trigger, clear) + b'\0'
            sock.send(ethernet(payload, code=0xfe, flags=0x50))
            assert receive(sock, 0xfe)[22 + len(object):26 + len(object)] == b'\xc7\x00\x82\x80'
            payload = b'\x11\x11\x11\x03' + object + b'\xc7\x00\x81\x06' + struct.pack('!HI', id, 2 if enabled else 1) + b'\0'
            sock.send(ethernet(payload, code=0xfe, flags=0x50))
            assert receive(sock, 0xfe)[22 + len(object):26 + len(object)] == b'\xc7\x00\x81\x80'

        def crc_record(generation, value):
            counters = [0] * 37
            counters[10] = value
            records = pm_file.read_bytes()
            pm_file.write_bytes(struct.pack('!38Q', generation, *counters) + records[304:])

        crc_record(4, 0x8000000001)
        counter_config(pm_object, 0x117, 100, 90, True)
        alarm = receive(sock, 1)
        assert alarm[20:22] == b'\xfe\x18'
        assert alarm[25:44] == struct.pack('!HIHHBQ', 4, 0, 0x117, 0, 0, 0x8000000001)
        tests.append('hardware CRC counter produces a24-byte event TLV with an uncorrupted64-bit cumulative value')
        pm_set(0xb2, b'\0')
        assert pm_get(0xb2) == bytes(296)
        try:
            receive(sock, 1, timeout=1.2)
            raise RuntimeError('PM baseline reset fabricated a counter recovery')
        except AssertionError:
            pass
        crc_record(5, 89)
        clear = receive(sock, 1)
        assert clear[25:44] == struct.pack('!HIHHBQ', 4, 0, 0x117, 0, 1, 89)
        tests.append('PM baseline reset preserves cumulative alarm state; actual hardware generation reset allows recovery')
        records = pm_file.read_bytes()
        pm_file.unlink()
        sock.send(ethernet(event_get, code=0xfe, flags=0x50))
        receive(sock, 0xfe)
        status(state_file, lambda s: s['alarm_error'] < 0 and s['pm_error'] < 0)
        try:
            receive(sock, 1, timeout=1.2)
            raise RuntimeError('failed hardware read fabricated a counter event')
        except AssertionError:
            pass
        pm_file.write_bytes(records)
        counter_config(pm_object, 0x117, 100, 90, False)
        tests.append('failed hardware counter read remains visible and suppresses stale notifications')
        uni_object = bytes.fromhex('37000104 01000001')
        changes = int(Path('/sys/class/net/lan4/carrier_changes').read_text())
        assert changes == 1
        counter_config(uni_object, 0x316, 0, 0, True)
        alarm = receive(sock, 1)
        assert alarm[25:44] == struct.pack('!HIHHBQ', 1, 0x01000001, 0x316, 0, 0, changes)
        counter_config(uni_object, 0x316, 0, 0, False)
        tests.append('CTC UNI1 link-change alarm reads lan4 native carrier_changes and treats a genuine count of1 as valid')
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
