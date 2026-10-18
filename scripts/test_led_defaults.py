#!/usr/bin/env python3
"""Run both boards' LAN/USB LED defaults and preserved-config migration with real UCI."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
tree = root / 'sources/openwrt'
base = root / 'port/overlay/target/linux/rtl9617c/base-files'
sys.path.insert(0, str(root / 'tools'))
from uci_config import command

with tempfile.TemporaryDirectory(prefix='led-defaults-') as directory:
    work = Path(directory)
    config, stage = work / 'config', work / 'stage'
    config.mkdir()
    stage.mkdir()
    cli = command() + ['-c', str(config), '-t', str(stage)]
    busybox, = tree.glob('build_dir/target-*/busybox-*/*/busybox')
    shell = command()[:-1] + [str(busybox), 'ash']
    wrapper = work / 'uci'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(cli) + ' "$@"\n')
    wrapper.chmod(0o755)
    env = dict(os.environ, PATH=str(work) + ':' + str(tree / 'staging_dir/host/bin') + ':' + os.environ['PATH'])
    uci_sh = work / 'uci.sh'
    uci_sh.write_text((tree / 'package/system/uci/files/lib/config/uci.sh').read_text()
                      .replace('/sbin/uci', str(wrapper)))
    functions = work / 'functions.sh'
    functions.write_text((tree / 'package/base-files/files/lib/functions.sh').read_text()
                         .replace('/lib/config/uci.sh', str(uci_sh)) + '\nLOAD_STATE=\n')
    system = work / 'system.sh'
    defaults = work / 'defaults.sh'
    defaults.write_text((tree / 'package/base-files/files/lib/functions/uci-defaults.sh').read_text()
        .replace('/lib/functions.sh', str(functions))
        .replace('/usr/share/libubox/jshn.sh', str(tree / 'staging_dir/host/share/libubox/jshn.sh')))
    for name, source in [('board.sh', base / 'etc/board.d/01_leds'),
                         ('migrate.sh', base / 'etc/uci-defaults/94-tyfza1-leds')]:
        (work / name).write_text(source.read_text()
            .replace('/lib/functions/uci-defaults.sh', str(defaults))
            .replace('/lib/functions/system.sh', str(system))
            .replace('/lib/functions.sh', str(functions)))
    for board, led in [('tianyi,tyfza1', 'LED_USB0'), ('tianyi,tyfza1-2p5g', 'LED_USB1')]:
        # Captured OEM PORT_REMAPPING=04,02,01,00 and EPHY0..3 -> eth0.2..5.
        lans = ['lan4', 'lan3', 'lan2', 'lan1'] if board.endswith('-2p5g') else ['lan1', 'lan2', 'lan3', 'lan4']
        system.write_text('board_name() { printf "%s\\n" ' + shlex.quote(board) + '; }\n')
        board_file = work / 'board.json'
        board_file.write_text(json.dumps({'model': {'id': board}}))
        subprocess.run(shell + [str(work / 'board.sh')], env=dict(env, CFG=str(board_file)), check=True)
        data = json.loads(board_file.read_text())
        assert data['led']['usb']['sysfs'] == led
        assert data['led']['usb']['trigger'] == 'tyfza1-usb'
        for port, dev in enumerate(lans):
            assert data['led'][f'lan{port}']['sysfs'] == f'LED_EPHY{port}'
            assert data['led'][f'lan{port}']['device'] == dev, (board, port, data['led'])
        for old in ('LED_USB0', 'LED_USB1'):
            (config / 'system').write_text("config led 'usb'\n option sysfs '" + old +
                "'\n option name 'My USB'\n option trigger 'usbport'\n" +
                "config led 'custom'\n option sysfs 'LED_EPHY0'\n option dev 'lan3'\n")
            subprocess.run(shell + [str(work / 'migrate.sh')], env=env, check=True)
            exported = subprocess.check_output(cli + ['export', 'system'])
            expected = led if board == 'tianyi,tyfza1-2p5g' else old
            assert subprocess.check_output(cli + ['get', 'system.usb.sysfs'], text=True).strip() == expected
            assert subprocess.check_output(cli + ['get', 'system.usb.name'], text=True).strip() == 'My USB'
            assert subprocess.check_output(cli + ['get', 'system.custom.dev'], text=True).strip() == 'lan3'
            for port in range(1, 4):
                assert subprocess.check_output(cli + ['get', f'system.led_lan{port}.dev'], text=True).strip() == lans[port]
            subprocess.run(shell + [str(work / 'migrate.sh')], env=env, check=True)
            assert subprocess.check_output(cli + ['export', 'system']) == exported
        (config / 'system').write_text(''.join(
            f"config led 'led_lan{port}'\n option name 'LAN{port + 1}'\n"
            f" option sysfs 'LED_EPHY{port}'\n option trigger 'netdev'\n"
            f" option dev 'lan{port + 1}'\n option mode 'link tx rx'\n"
            for port in range(4)) +
            f"config led 'led_usb'\n option sysfs '{led}'\n option trigger 'usbport'\n"
            " list port 'usb1-port1'\n list port 'usb2-port1'\n")
        subprocess.run(shell + [str(work / 'migrate.sh')], env=env, check=True)
        assert subprocess.check_output(cli + ['get', 'system.led_usb.trigger'], text=True).strip() == 'tyfza1-usb'
        for port, dev in enumerate(lans):
            assert subprocess.check_output(cli + ['get', f'system.led_lan{port}.dev'], text=True).strip() == dev
        exported = subprocess.check_output(cli + ['export', 'system'])
        subprocess.run(shell + [str(work / 'migrate.sh')], env=env, check=True)
        assert subprocess.check_output(cli + ['export', 'system']) == exported
        # A changed netdev or trigger is not the old generated binding.
        subprocess.run(cli + ['set', 'system.led_lan0.dev=lan4'], check=True)
        subprocess.run(cli + ['set', 'system.led_lan1.dev=lan2'], check=True)
        subprocess.run(cli + ['set', 'system.led_lan1.trigger=timer'], check=True)
        subprocess.run(cli + ['commit', 'system'], check=True)
        exported = subprocess.check_output(cli + ['export', 'system'])
        subprocess.run(shell + [str(work / 'migrate.sh')], env=env, check=True)
        assert subprocess.check_output(cli + ['export', 'system']) == exported
print('Both boards: LAN/USB defaults, old LAN/USB migration, custom bindings and repeated boot passed')
