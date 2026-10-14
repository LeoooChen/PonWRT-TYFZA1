#!/usr/bin/env python3
"""Execute the PON DHCP defaults against actual OpenWrt UCI in a fixture."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from uci_config import command
source = root / 'port/overlay/target/linux/rtl9617c/base-files/etc/uci-defaults/90-tyfza1-pon-wan'
with tempfile.TemporaryDirectory(prefix='pon-wan-test-', dir=root / 'artifacts') as directory:
    directory = Path(directory)
    config = directory / 'config'
    config.mkdir()
    stage = directory / 'stage'
    stage.mkdir()
    cli = command() + ['-c', str(config), '-t', str(stage)]
    wrapper = directory / 'uci'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(cli) + ' "$@"\n')
    wrapper.chmod(0o755)
    network = config / 'network'
    network.write_text("config interface 'lan'\n option proto 'static'\n option ipaddr '192.168.1.1'\n")
    binding = directory / 'llid_index'
    binding.write_text('0\n')
    script = directory / 'default.sh'
    script.write_text(source.read_text().replace('/sys/class/net/pon-data0/llid_index', str(binding)))
    subprocess.run(['sh', '-c', 'PATH=' + shlex.quote(str(directory)) + ':"$PATH" sh ' + shlex.quote(str(script))], check=True)
    def get(key):
        return subprocess.check_output(cli + ['get', key], text=True).strip()
    assert get('network.wan.device') == 'pon-data0' and get('network.wan.proto') == 'dhcp'
    assert get('network.lan.ipaddr') == '192.168.1.1'
    assert get('network.wan6.device') == '@wan'
    assert get('network.wan6.proto') == 'dhcpv6'
    assert get('network.wan.ipv6') == '1'
    subprocess.run(cli + ['set', 'network.wan.device=pon-data0.100'], check=True)
    subprocess.run(cli + ['commit', 'network'], check=True)
    subprocess.run(['sh', '-c', 'PATH=' + shlex.quote(str(directory)) + ':"$PATH" sh ' + shlex.quote(str(script))], check=True)
    assert get('network.wan.device') == 'pon-data0.100'
    subprocess.run(cli + ['set', 'network.wan.proto=pppoe'], check=True)
    subprocess.run(cli + ['set', 'network.wan.ipv6=0'], check=True)
    subprocess.run(cli + ['commit', 'network'], check=True)
    subprocess.run(['sh', str(script)], env={'PATH':str(directory)+':/usr/bin:/bin'}, check=True)
    assert get('network.wan.ipv6') == '0'
    assert get('network.wan6.device') == '@wan'
(root / 'logs/pon-wan-default-test.json').write_text(json.dumps({'actual_uci':True,
    'DHCP_device':'pon-data0','existing_WAN_preserved':True,'LAN_preserved':True,'hardware_verified':False},indent=2)+'\n')
print('Actual PON WAN defaults: fresh DHCP binding, LAN preserved and existing WAN untouched passed')
