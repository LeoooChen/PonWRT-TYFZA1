#!/usr/bin/env python3
"""Find a USB UART, run picocom, and preserve raw RX plus timestamped TX."""
import argparse
import datetime
import glob
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import termios
import time
import tty


def ports():
    return sorted(set(glob.glob('/dev/cu.usbserial*') +
                      glob.glob('/dev/cu.usbmodem*') +
                      glob.glob('/dev/cu.wchusbserial*') +
                      glob.glob('/dev/cu.SLAB_USBtoUART*')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--port', help='Explicit port, otherwise require one USB UART')
    parser.add_argument('--stop-autoboot', action='store_true',
                        help='Send Ctrl-C immediately when the U-Boot countdown appears')
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(__file__).resolve().parents[1]
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    events = (logs / f'serial-{stamp}.events.jsonl').open('a', buffering=1)
    raw = (logs / f'serial-{stamp}.rx.bin').open('ab', buffering=0)
    transcript = (logs / f'serial-{stamp}.txt').open('ab', buffering=0)

    def event(kind, **fields):
        events.write(json.dumps({'time': datetime.datetime.now().astimezone().isoformat(),
                                 'kind': kind, **fields}, ensure_ascii=True) + '\n')

    original = termios.tcgetattr(sys.stdin.fileno()) if sys.stdin.isatty() else None
    child = None
    master = None
    try:
        if original:
            tty.setraw(sys.stdin.fileno())
        while True:
            candidates = ([args.port] if Path(args.port).exists() else []) if args.port else ports()
            if len(candidates) != 1:
                event('waiting', candidates=candidates)
                sys.stdout.write(f'\r\nWaiting for one USB UART: {candidates}\r\n')
                sys.stdout.flush()
                readable, _, _ = select.select([sys.stdin.fileno()], [], [], 3)
                if readable:
                    data = os.read(sys.stdin.fileno(), 65536)
                    event('input-while-disconnected', hex=data.hex())
                    if not data or b'\x01\x11' in data or b'\x01\x18' in data or b'\x03' in data:
                        return
                continue
            port = candidates[0]
            master, slave = pty.openpty()
            command = [shutil.which('picocom') or 'picocom', '--baud', str(args.baud),
                       '--flow', 'n', '--parity', 'n', '--databits', '8',
                       '--stopbits', '1', '--noreset', port]
            event('connect', port=port, baud=args.baud, command=command)
            child = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                     cwd=root, close_fds=True)
            os.close(slave)
            recent = b''
            while child.poll() is None:
                readable, _, _ = select.select([master, sys.stdin.fileno()], [], [], 1)
                if master in readable:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    raw.write(data)
                    transcript.write(data)
                    os.write(sys.stdout.fileno(), data)
                    recent = (recent + data)[-512:]
                    if args.stop_autoboot and b'Hit any key to stop autoboot:' in recent:
                        event('tx', hex='03', text='\x03', reason='stop-autoboot')
                        os.write(master, b'\x03')
                        recent = b''
                if sys.stdin.fileno() in readable:
                    data = os.read(sys.stdin.fileno(), 65536)
                    if not data:
                        return
                    event('tx', hex=data.hex(), text=data.decode('utf-8', 'replace'))
                    os.write(master, data)
            code = child.wait()
            event('disconnect', port=port, returncode=code)
            os.close(master)
            master = None
            child = None
            if code == 0:
                return
            time.sleep(2)
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        if master is not None:
            os.close(master)
        if original:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)
        event('exit')
        events.close()
        raw.close()
        transcript.close()


if __name__ == '__main__':
    main()
