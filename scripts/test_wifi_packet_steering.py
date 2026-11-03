#!/usr/bin/env python3
"""Execute board steering policy with real shell and isolated IRQ/RPS files."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/base-files/usr/libexec/platform/packet-steering.sh').read_text()
with tempfile.TemporaryDirectory(prefix='wifi-steering-') as temp:
    work = Path(temp)
    for name in ['sys/class/net/wlan0/device', 'sys/class/net/wlan0/queues/rx-0',
                 'sys/class/net/usb0/device', 'sys/class/net/usb0/queues/rx-0', 'proc/irq/42']:
        (work / name).mkdir(parents=True)
    (work / 'sys/class/net/wlan0/device/driver').symlink_to('/drivers/rtl_ax_ap')
    (work / 'sys/class/net/usb0/device/driver').symlink_to('/drivers/other')
    (work / 'proc/interrupts').write_text('42: 0 0 0 0 GICv3 rtl9617c-rx0\n')
    generic = work / 'generic'
    generic.write_text('#!/bin/sh\nexit 0\n')
    generic.chmod(0o755)
    script = work / 'steer.sh'
    script.write_text(source.replace('/usr/libexec/network/packet-steering.uc', str(generic))
                     .replace('/sys/', str(work / 'sys') + '/')
                     .replace('/proc/', str(work / 'proc') + '/'))
    native = work / 'sys/class/net/wlan0/queues/rx-0/rps_cpus'
    other = work / 'sys/class/net/usb0/queues/rx-0/rps_cpus'
    irq = work / 'proc/irq/42/smp_affinity'
    for args, expected_irq, expected_native in [([], '4', '8'), (['1'], '4', '8'), (['0'], 'f', 'a')]:
        native.write_text('a\n')
        other.write_text('b\n')
        irq.write_text('f\n')
        subprocess.run(['sh', str(script), *args], check=True)
        assert irq.read_text().strip() == expected_irq
        assert native.read_text().strip() == expected_native
        assert other.read_text().strip() == 'b'
print('Wi-Fi steering: CPU2 NI / CPU3 local ingress, both enabled modes, disable and unrelated radios preserved')
