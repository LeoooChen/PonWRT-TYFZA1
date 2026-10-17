#!/usr/bin/env python3
"""Render real firewall4 rules with both netifd and legacy lower-device schemas."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
tree = root / 'sources/openwrt'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--unpatched', action='store_true', help='Reproduce against the pinned upstream source')
args = parser.parse_args()
package = (tree / 'package/network/config/firewall4/Makefile').read_text()
date = re.search(r'^PKG_SOURCE_DATE:=(.+)$', package, re.M)[1].replace('-', '.')
revision = re.search(r'^PKG_SOURCE_VERSION:=(.+)$', package, re.M)[1]
name = f'firewall4-{date}~{revision[:8]}'
archive = tree / 'dl' / (name + '.tar.zst')
ucode = tree / 'staging_dir/hostpkg/bin/ucode'

with tempfile.TemporaryDirectory(prefix='fw4-lower-devices-', dir=root / 'artifacts') as work:
    work = Path(work)
    subprocess.run(['tar', '--zstd', '-xf', str(archive), '-C', str(work)], check=True)
    source = work / name
    if not args.unpatched:
        subprocess.run(['patch', '-p1', '-i', str(root / 'port/host-patches/120-firewall4-netifd-lower-devices.patch')], cwd=source, check=True)
    fixtures = work / 'fixtures'

    def fixture(path, data):
        output = fixtures / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data))

    # The explicit netifd VLAN classes all recurse; leaf/tunnel devices do not.
    status = {
        'pon0.41': {'type': 'VLAN'},
        'lan-q': {'type': '8021q'},
        'lan-ad': {'type': '8021ad'},
        'br-test': {'type': 'bridge'},
        'legacy-vlan': {'type': 'Network device', 'devtype': 'vlan'},
        'legacy-br': {'type': 'Network device', 'devtype': 'bridge'},
        'lan3': {'type': 'Network device', 'devtype': 'dsa'},
        'pppoe-test': {'type': 'tunnel', 'devtype': 'ppp'},
        'unknown': {},
    }
    lowers = {
        'pon0.41': ['pon0'], 'lan-q': ['lan4'], 'lan-ad': ['lan-q'],
        'br-test': ['lan-ad', 'lan5'], 'legacy-vlan': ['eth5'],
        'legacy-br': ['eth6'], 'pppoe-test': ['eth7'], 'unknown': ['eth8'],
    }
    for device in status.keys() | {d for values in lowers.values() for d in values}:
        entries = ['.', '..', 'ifindex'] + ['lower_' + d for d in lowers.get(device, [])]
        key = re.sub(r'[^A-Za-z0-9_-]+', '_', device)
        fixture(f'fs/opendir~_sys_class_net_{key}.json', entries)
    interfaces = [{'interface': f'n{i}', 'up': True, 'device': d, 'l3_device': d, 'proto': 'static'}
                  for i, d in enumerate(status)]
    fixture('ubus/network.device~status.json', status)
    fixture('ubus/network.interface~dump.json', {'interface': interfaces})
    fixture('uci/firewall.json', {
        'defaults': {'flow_offloading': '1', 'flow_offloading_hw': '1'},
        'zone': [{'name': 'test', 'network': [i['interface'] for i in interfaces], 'auto_helper': '0'}],
    })
    fixture('uci/helpers.json', {})
    fixture('uci/ubus.json', {})
    # Load and render the complete production module and ruleset template.
    script = '{% include("./root/usr/share/firewall4/main.uc", {getenv: v => v == "ACTION" ? "print" : null}) %}'
    result = subprocess.run([
        str(ucode), '-S', '-T,', '-L./tests/lib', '-L./root/usr/share/ucode',
        '-D', 'MOCK_SEARCH_PATH=' + json.dumps([str(fixtures), './tests/mocks']),
        '-l', 'mocklib', '-l', 'fw4', '-',
    ], input=script, text=True, capture_output=True, cwd=source, check=True)
    flowtable = re.search(r'flowtable ft \{(.*?)\n\t\}', result.stdout, re.S)[1]
    devices = re.search(r'devices = \{ (.*?) \};', flowtable)[1]
    actual = re.findall(r'"([^"]+)"', devices)
    expected = sorted(['lan3', 'lan4', 'lan5', 'eth5', 'eth6', 'pon0', 'pppoe-test', 'unknown'])
    assert actual == expected, (actual, expected, result.stderr)
    assert 'flags offload;' in flowtable
    print('firewall4: full ruleset resolves native/8021q/8021ad VLAN, nested/legacy bridge, PPP and unknown leaves')
    # Existing upstream fixtures protect unrelated firewall behavior.
    env = dict(os.environ, PATH=str(ucode.parent) + ':' + os.environ['PATH'])
    subprocess.run(['bash', './run_tests.sh'], cwd=source, env=env, check=True)
