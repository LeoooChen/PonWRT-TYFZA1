#!/usr/bin/env python3
"""Run the packaged musl UCI converter, original writer comparisons and resident wire path."""
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


def quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--openwrt', type=Path, required=True)
    parser.add_argument('--manager', type=Path, required=True)
    parser.add_argument('--mib', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip', '-j', 'link'], text=True))} == {'lo'}
    loader = next(args.openwrt.glob('staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/lib/libc.so'))
    libs = args.openwrt / 'staging_dir/target-aarch64_cortex-a55_musl/usr/lib'
    prefix = [str(loader), '--library-path', str(libs) + ':' + str(loader.parent)]
    converter = prefix + [str(args.stage / 'usr/sbin/fttr-sync-policy')]
    controller = prefix + [str(args.stage / 'usr/sbin/fttr-sync')]
    stock = WifiFieldsOriginal(args.manager, args.mib)
    host = bytes.fromhex('020304050607'); peer_mac = stock.peer
    group = bytes.fromhex('0110ec000002'); master = bytes.fromhex('0110ec000001')
    policy = {'Peers': [{'MAC': peer_mac.hex(':'), 'Registration': {'OUI': '020304', 'SN': 'synthetic-host',
                         'Ready': 1, 'ExpectedSN': 'synthetic-peer'},
                         'WiFiFromUCI': [{'Section': 'ap_low', 'SSIDIndex': '1', 'Standard': '11bgn'},
                                         {'Section': 'ap_high', 'SSIDIndex': '9', 'Standard': '11ax'}]}]}
    radio = {'band': '2g', 'disabled': '0'}
    vif = {'device': 'radio_low', 'mode': 'ap', 'ssid': "do-not-print-中文'\\$()", 'encryption': 'psk2',
           'key': "do-not-print-key'\\$()", 'disabled': '0', 'hidden': '0'}
    high = dict(vif, device='radio_high', ssid='do-not-print-high', encryption='sae-mixed')
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        source = folder / 'wireless'; binding = folder / 'binding.json'; output = folder / 'policy.json'

        def wireless(device, interface):
            sections = [('wifi-device', 'radio_low', device), ('wifi-iface', 'ap_low', interface),
                        ('wifi-device', 'radio_high', {'band': '5g'}), ('wifi-iface', 'ap_high', high)]
            source.write_text('\n'.join('config ' + typ + ' ' + quote(name) + '\n' + '\n'.join(
                '\t' + ('list' if isinstance(value, list) else 'option') + ' ' + key + ' ' + quote(value[0] if isinstance(value, list) else value)
                for key, value in values.items()) for typ, name, values in sections) + '\n')
            source.chmod(0o600)

        cases = []; originals = 0
        def run(case):
            nonlocal originals
            desired = copy.deepcopy(policy); device = copy.deepcopy(radio); interface = copy.deepcopy(vif)
            binding.unlink(missing_ok=True); source.unlink(missing_ok=True); output.unlink(missing_ok=True)
            if case in ('none', 'psk', 'psk-mixed', 'sae', 'sae-mixed'): interface['encryption'] = case
            if case == 'none': del interface['key']
            if case == 'disabled': interface['disabled'] = '1'
            if case == 'radio-disabled': device['disabled'] = 'true'
            if case == 'hidden': interface['hidden'] = 'yes'
            if case == 'defaults':
                for k in ('disabled', 'hidden'): del interface[k]
            if case == 'ssid32': interface['ssid'] = 'S' * 32
            if case == 'wep': interface.update(encryption='wep-open', key='1', key1='abcde')
            if case in ('cipher', 'enterprise', 'owe'): interface['encryption'] = {'cipher': 'psk2+tkip', 'enterprise': 'wpa2', 'owe': 'owe'}[case]
            if case == 'sae-password': interface['sae_password'] = ['do-not-print-other']
            if case == 'psk-file': interface['wpa_psk_file'] = '/private-keys'
            if case == 'pmf': interface['ieee80211w'] = '2'
            if case == 'missing-key': del interface['key']
            if case == 'list-key': interface['key'] = ['do-not-print-key']
            if case == 'open-key': interface['encryption'] = 'none'
            if case == 'short-key': interface['key'] = 'short'
            if case == 'hex-psk': interface['key'] = 'a' * 64
            if case == 'long-ssid': interface['ssid'] = 'x' * 33
            if case == 'bad-boolean': interface['disabled'] = 'banana'
            if case == 'list-boolean': interface['hidden'] = ['1']
            if case == 'sta': interface['mode'] = 'sta'
            if case == 'missing-band': del device['band']
            if case == '6g': device['band'] = '6g'
            if case == 'wrong-band': device['band'] = '5g'
            if case == 'missing-radio': interface['device'] = 'absent'
            if case == 'missing-section': desired['Peers'][0]['WiFiFromUCI'][0]['Section'] = 'absent'
            if case == 'positional': desired['Peers'][0]['WiFiFromUCI'][0]['Section'] = '@wifi-iface[0]'
            if case == 'invalid-index': desired['Peers'][0]['WiFiFromUCI'][0]['SSIDIndex'] = '01'
            if case == 'invalid-standard': desired['Peers'][0]['WiFiFromUCI'][0]['Standard'] = 'HE80'
            if case == 'duplicate-index': desired['Peers'][0]['WiFiFromUCI'].append(desired['Peers'][0]['WiFiFromUCI'][0])
            if case == 'duplicate-peer': desired['Peers'].append(desired['Peers'][0])
            if case == 'missing-ready': del desired['Peers'][0]['Registration']['Ready']
            if case == 'mixed-input': desired['Peers'][0]['WiFi'] = []
            if case == 'extra-field': desired['Peers'][0]['WiFiFromUCI'][0]['Default'] = True
            wireless(device, interface)
            binding.write_text(json.dumps(desired)); binding.chmod(0o600)
            if case == 'bad-uci': source.write_text("config 'unterminated")
            if case == 'invalid-utf8': source.write_bytes(source.read_bytes().replace(b'do-not-print-', b'\xff', 1))
            if case == 'public-uci': source.chmod(0o644)
            if case == 'public-binding': binding.chmod(0o644)
            if case == 'large-uci': source.write_text('#' * 131073)
            if case == 'symlink-uci':
                target = folder / 'wireless-target'; target.write_bytes(source.read_bytes()); target.chmod(0o600)
                source.unlink(); source.symlink_to(target)
            if case in ('existing-output', 'output-symlink'):
                output.write_bytes(b'existing')
                if case == 'output-symlink':
                    target = folder / 'existing-target'; target.write_bytes(b'existing')
                    output.unlink(); output.symlink_to(target)
            result = subprocess.run(converter + [str(source), str(binding), str(output)], capture_output=True, text=True)
            assert not result.stdout and 'do-not-print' not in result.stderr, case
            success = case in ('normal', 'none', 'psk', 'psk-mixed', 'sae', 'sae-mixed', 'disabled', 'radio-disabled', 'hidden', 'defaults', 'ssid32')
            assert result.returncode == (0 if success else 1), (case, result.stderr)
            if success:
                assert output.stat().st_mode & 0o777 == 0o600
                generated = json.loads(output.read_text())
                assert generated['Peers'][0]['Registration'] == policy['Peers'][0]['Registration']
                rows = generated['Peers'][0]['WiFi']
                assert len(rows) == 2 and rows[0]['SSID'] == interface['ssid']
                assert rows[0]['Enable'] == int(case not in ('disabled', 'radio-disabled'))
                assert rows[0]['SSIDAdvertisementEnabled'] == ('false' if case == 'hidden' else 'true')
                assert rows[0]['PWD'] == interface.get('key', '')
                for row in rows:
                    value = {k: str(v) for k, v in row.items() if k != 'act'}
                    value['SSIDIndex'] = str(16 + int(row['SSIDIndex']))
                    value['ENCRYPT'] = str({0:1, 2:3, 4:4, 6:5, 16:9, 20:10}[row['ENCRYPT']])
                    if row['ENCRYPT'] == 0: del value['PWD']
                    expected = copy.deepcopy(row)
                    if case == 'ssid32': expected['SSID'] = expected['SSID'][:31]
                    assert stock.write(value)['content'] == expected, case
                    originals += 1
            elif case in ('existing-output', 'output-symlink'):
                assert output.read_bytes() == b'existing'
            else:
                assert not output.exists(), case
            assert not list(folder.glob('policy.json.*')), case
            cases.append(case)

        for case in ('normal', 'none', 'psk', 'psk-mixed', 'sae', 'sae-mixed', 'disabled', 'radio-disabled', 'hidden', 'defaults', 'ssid32',
                     'wep', 'cipher', 'enterprise', 'owe', 'sae-password', 'psk-file', 'pmf', 'missing-key', 'list-key', 'open-key',
                     'short-key', 'hex-psk', 'long-ssid', 'bad-boolean', 'list-boolean', 'sta', 'missing-band', '6g', 'wrong-band',
                     'missing-radio', 'missing-section', 'positional', 'invalid-index', 'invalid-standard', 'duplicate-index',
                     'duplicate-peer', 'missing-ready', 'mixed-input', 'extra-field', 'bad-uci', 'invalid-utf8', 'public-uci', 'public-binding',
                     'large-uci', 'symlink-uci', 'existing-output', 'output-symlink'):
            run(case)
        run('normal')
        generated = json.loads(output.read_text())['Peers'][0]['WiFi']
        subprocess.run(['ip', 'link', 'add', 'uci-host', 'type', 'veth', 'peer', 'name', 'uci-peer'], check=True)
        for interface, mac in (('uci-host', host), ('uci-peer', peer_mac)):
            subprocess.run(['ip', 'link', 'set', interface, 'address', mac.hex(':'), 'mtu', '1982', 'up'], check=True)
        remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x88aa))
        remote.bind(('uci-peer', 0x88aa)); remote.setblocking(False)
        state = stock.read_local()['List']
        for entry in state[8:]: entry['Standard'] = '11ax'
        def send(value):
            body = json.dumps(value, separators=(',', ':')).encode()
            body = len(body).to_bytes(4, 'big') + body
            size = 1200; count = (len(body) + size - 1) // size
            for i in reversed(range(count)):
                remote.send(master + peer_mac + b'\x88\xaa\xf6' + bytes([(count - 1) << 5 | i << 2 | 1]) + body[i * size:(i + 1) * size])
        with (folder / 'events').open('w+') as events, (folder / 'errors').open('w+') as errors:
            proc = subprocess.Popen(controller + ['uci-host', 'serve', '0x88aa', str(output)], stdout=events, stderr=errors)
            deadline = time.monotonic() + 3; writes = 0; gets = 0
            try:
                while time.monotonic() < deadline:
                    if not select.select([remote], [], [], 0.02)[0]: continue
                    frame, address = remote.recvfrom(4096)
                    if address[2] == socket.PACKET_OUTGOING: continue
                    assert frame[:12] == group + host
                    if frame[14] & 15 == 1:
                        remote.send(master + peer_mac + b'\x88\xaa\x01\0')
                        if gets == 0: send({'RPCMethod': 'Register', 'SN': 'synthetic-peer'})
                        continue
                    if frame[14] & 15 == 7: continue
                    value = json.loads(frame[20:]); assert value['DMAC'] == peer_mac.hex().upper()
                    content = value['content']
                    if content['act'] == 'get':
                        gets += 1
                        send({'RPCMethod': 'SetAllSetting', 'Flag': 1, 'STATUS': 0, 'content': {'content': {'List': state}}})
                    else:
                        assert content == generated[writes]
                        entry = state[int(content['SSIDIndex']) - 1]
                        for key in ('SSID', 'Standard', 'SSIDAdvertisementEnabled'): entry[key] = content[key]
                        entry['Enable'] = str(content['Enable'])
                        entry['ENCRYPT'] = str({4:4, 20:10}[content['ENCRYPT']])
                        writes += 1; send({'Result': 1})
                proc.terminate(); proc.wait(timeout=2)
            finally:
                if proc.poll() is None: proc.kill(); proc.wait()
            events.seek(0); event_text = events.read(); errors.seek(0); error_text = errors.read()
        assert proc.returncode == 0 and writes == 2 and gets == 4, (writes, gets, error_text)
        assert 'do-not-print' not in event_text + error_text
        applied = [json.loads(line) for line in event_text.splitlines() if json.loads(line)['event'] == 'sync_resident_apply']
        assert len(applied) == 2 and all(e['result'] == 0 and e['readable_fields_match'] and not e['credentials_verified'] for e in applied)
        remote.close()
    result = {'converter_cases': cases, 'original_writer_comparisons': originals,
              'resident_wire': {'writes': writes, 'gets': gets, 'readable_fields_match': True, 'credentials_verified': False},
              'scope': 'Actual packaged AArch64/musl converter with real libuci, retained original Wi-Fi builder, and packaged resident daemon over veth. No actual slave, CWMP readiness or credential installation.'}
    args.output.write_text(json.dumps(result, indent=2) + '\n'); print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
