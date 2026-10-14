#!/usr/bin/env python3
"""Load the verified Wi-Fi NE FIT using the project's existing UART/TFTP service."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import termios
import time

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--stock-console', action='store_true')
parser.add_argument('--raw', action='store_true')
parser.add_argument('--payload', default='full-pon-wifi-ne-live06-6.18.55')
parser.add_argument('--at-uboot', action='store_true')
parser.add_argument('--capture-runtime', action='store_true')
parser.add_argument('--port', default='/dev/cu.usbserial-1120')
parser.add_argument('--relay-port', help='LCUS-1 at9600baud, ONU power wired to COM/NC')
parser.add_argument('--tftp-root', type=Path)
parser.add_argument('--ram-variant', default='ram-pcie')
args = parser.parse_args()
variant = 'ram-pcie-raw' if args.raw else args.ram_variant
payload = root / ('artifacts/' + args.payload + '/' + variant + '/image.itb')
suffix = '-raw' if args.raw else ('' if variant == 'ram-pcie' else '-' + variant)
verification = json.loads((payload.parents[1] / ('wifi-ne-code-runtime' + suffix + '-verification.json')).read_text())
assert hashlib.sha256(payload.read_bytes()).hexdigest() == verification['FIT_sha256']
tftp_name = 'tyfza1-wifi-ne05.itb'
shutil.copyfile(payload, root / 'artifacts/ram-boot-radio-c-tssi' / tftp_name)
if args.tftp_root:
    shutil.copyfile(payload, args.tftp_root / tftp_name)
private = root / 'artifacts/wifi-ne-live-config-20261010'
transcript = (private / 'boot-serial.rx.bin').open('ab', buffering=0)
fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
original = termios.tcgetattr(fd)
settings = original.copy()
settings[6] = original[6].copy()
settings[0] = settings[1] = settings[3] = 0
settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
settings[4] = settings[5] = termios.B115200
settings[6][termios.VMIN] = 0
settings[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, settings)
recent = b''
events = []

def event(message):
    events.append({'monotonic': time.monotonic(), 'message': message})
    (private / 'boot-events.json').write_text(json.dumps(events, indent=2) + '\n')
    print(message, flush=True)

def read_uart(wait=0.1):
    global recent
    if select.select([fd], [], [], wait)[0]:
        data = os.read(fd, 65536)
        transcript.write(data)
        recent = (recent + data)[-65536:]

def wait_for(pattern, seconds):
    deadline = time.monotonic() + seconds
    while pattern not in recent:
        if time.monotonic() >= deadline:
            raise TimeoutError('UART did not reach ' + pattern.decode('ascii', 'replace'))
        read_uart()

def command(line, response=b'TAURUS#', seconds=10):
    global recent
    recent = b''
    # U-Boot's UART input loses bytes when a whole command arrives at once.
    for value in line.encode() + b'\r':
        os.write(fd, bytes([value]))
        time.sleep(0.002)
    wait_for(response, seconds)

try:
    event('Verified FIT and current configurations; UART/TFTP RAM boot starting.')
    # The host already has en5 address192.168.1.170 and the project TFTP daemon.
    if args.at_uboot:
        os.write(fd, b'\x03')
        wait_for(b'TAURUS#', 10)
    elif args.relay_port:
        relay = os.open(args.relay_port, os.O_RDWR | os.O_NOCTTY)
        relay_settings = termios.tcgetattr(relay)
        relay_settings[0] = relay_settings[1] = relay_settings[3] = 0
        relay_settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        relay_settings[4] = relay_settings[5] = termios.B9600
        termios.tcsetattr(relay, termios.TCSANOW, relay_settings)
        os.write(relay, bytes.fromhex('a0 01 01 a2'))
        termios.tcdrain(relay)
        time.sleep(2)
        os.write(relay, bytes.fromhex('a0 01 00 a1'))
        termios.tcdrain(relay)
        os.close(relay)
        recent = b''
        event('LCUS-1 COM/NC power cycle complete; awaiting U-Boot.')
    elif args.stock_console:
        # Use the existing backup collector's authenticated console account.
        import ast
        collector = Path('/Users/leo/Documents/Codex项目/TYFZA1/串口记录&备份/scripts/device_link.py')
        calls = [node for node in ast.walk(ast.parse(collector.read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'send']
        user = next(ast.literal_eval(node.args[0]) for node in calls
                    if node.args and isinstance(node.args[0], ast.Constant) and not node.keywords)
        password = next(ast.literal_eval(node.args[0]) for node in calls
                        if any(k.arg == 'secret' for k in node.keywords))
        recent = b''
        os.write(fd, b'\recho __PON_RAM_SHELL__; id\r')
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline: read_uart()
        for attempt in range(3):
            if b'uid=0(root)' in recent: break
            os.write(fd, (user.strip() + '\r').encode())
            end = time.monotonic() + 1
            while time.monotonic() < end: read_uart()
            os.write(fd, (password.strip() + '\r').encode())
            end = time.monotonic() + 2
            while time.monotonic() < end: read_uart()
            os.write(fd, b'echo __PON_RAM_SHELL__; id\r')
            end = time.monotonic() + 2
            while time.monotonic() < end: read_uart()
            if b'uid=0(root)' in recent: break
        else: raise RuntimeError('Stock console login did not establish root shell')
        event('Authenticated stock console; rebooting to the RAM loader.')
        recent = b''
        os.write(fd, b'reboot\r')
    else:
        with (private / 'reboot-ssh.log').open('ab') as output:
            process = subprocess.Popen(['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
                                        '-o', 'StrictHostKeyChecking=yes', 'root@192.168.1.1', 'reboot'],
                                       stdout=output, stderr=output)
    if not args.at_uboot:
        wait_for(b'Hit any key to stop autoboot:', 60)
        os.write(fd, b'\x03')
        wait_for(b'TAURUS#', 10)
    event('U-Boot stopped; loading the signed RAM FIT.')
    command('setenv serverip 192.168.1.170')
    command('setenv ipaddr 192.168.1.2')
    command('tftpboot 0x05000000 ' + tftp_name, b'Bytes transferred =', 120)
    wait_for(b'TAURUS#', 5)
    command('source 0x05000000:script', b'Linux version 6.18.55', 40)
    event('Native kernel started; waiting for APs and SSH.')
    deadline = time.monotonic() + 180
    next_check = 0
    while time.monotonic() < deadline:
        read_uart()
        if time.monotonic() < next_check:
            continue
        next_check = time.monotonic() + 3
        result = subprocess.run(['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
                                 '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=2',
                                 'root@192.168.1.1', 'ubus -S call network.wireless status'],
                                capture_output=True, timeout=5)
        if result.returncode:
            continue
        try:
            status = json.loads(result.stdout)
        except ValueError:
            continue
        if status.get('tyfza1_2g', {}).get('up') and status.get('tyfza1_5g', {}).get('up'):
            event('Both native APs are up; SSH identity preserved.')
            break
    else:
        raise TimeoutError('AP startup did not finish within180seconds; UART transcript retained')
    if args.capture_runtime:
        event('Continuous runtime UART capture active.')
        while True:
            read_uart(1)
finally:
    termios.tcsetattr(fd, termios.TCSANOW, original)
    os.close(fd)
    transcript.close()
