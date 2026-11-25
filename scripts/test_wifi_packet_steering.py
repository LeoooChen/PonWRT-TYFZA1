#!/usr/bin/env python3
"""Execute board steering policy with real shell and isolated IRQ/RPS files."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/base-files/usr/libexec/platform/packet-steering.sh').read_text()
with tempfile.TemporaryDirectory(prefix='wifi-steering-') as temp:
    work = Path(temp)
    for name in ['sys/class/net/wlan0/device', 'sys/class/net/wlan0/queues/rx-0',
                 'sys/class/net/usb0/device', 'sys/class/net/usb0/queues/rx-0',
                 'sys/class/net/lan4', 'proc/irq/42', 'proc/167']:
        (work / name).mkdir(parents=True)
    (work / 'sys/class/net/wlan0/device/driver').symlink_to('/drivers/rtl_ax_ap')
    (work / 'sys/class/net/usb0/device/driver').symlink_to('/drivers/other')
    (work / 'proc/interrupts').write_text('42: 0 0 0 0 GICv3 rtl9617c-rx0\n')
    generic = work / 'generic'
    generic.write_text('#!/bin/sh\nexit 0\n')
    generic.chmod(0o755)
    (work / 'proc/167/comm').write_text('napi/lan4-231\n')
    taskset = work / 'taskset'
    affinity = work / 'napi_affinity'
    taskset.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" > "{affinity}"\n')
    taskset.chmod(0o755)
    script = work / 'steer.sh'
    script.write_text(source.replace('/usr/libexec/network/packet-steering.uc', str(generic))
                     .replace('/sys/', str(work / 'sys') + '/')
                     .replace('/proc/', str(work / 'proc') + '/'))
    native = work / 'sys/class/net/wlan0/queues/rx-0/rps_cpus'
    other = work / 'sys/class/net/usb0/queues/rx-0/rps_cpus'
    irq = work / 'proc/irq/42/smp_affinity'
    threaded = work / 'sys/class/net/lan4/threaded'
    env = {**os.environ, 'PATH': f'{work}:{os.environ["PATH"]}'}
    for args, expected_irq, expected_native in [([], '4', 'a'), (['1'], '4', 'a'), (['0'], 'f', '5')]:
        native.write_text('5\n')
        other.write_text('b\n')
        irq.write_text('f\n')
        affinity.write_text('')
        subprocess.run(['sh', str(script), *args], check=True, env=env)
        assert irq.read_text().strip() == expected_irq
        assert threaded.read_text().strip() == ('1' if expected_irq == '4' else '0')
        assert affinity.read_text().strip() == ('-p 4 167' if expected_irq == '4' else '')
        assert native.read_text().strip() == expected_native
        assert other.read_text().strip() == 'b'
print('Wi-Fi steering: threaded NI on CPU2, local ingress on CPU1/3, disable and unrelated radios preserved')
