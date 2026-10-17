#!/usr/bin/env python3
"""Exercise preserved-config device migration with the actual target UCI module."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from uci_config import command

source = root / 'port/overlay/target/linux/rtl9617c/base-files/usr/share/tyfza1/rename-netdevs.uc'
ucode = next((root / 'sources/openwrt/build_dir/target-aarch64_cortex-a55_musl').glob('ucode-*/ucode'))
runtime = command()[:-1] + [str(ucode), '-L', str(ucode.parent / '*.so')]

with tempfile.TemporaryDirectory(prefix='netdev-rename-', dir=root / 'artifacts') as work:
    work = Path(work)
    config = work / 'config'
    stage = work / 'stage'
    config.mkdir()
    stage.mkdir()
    files = {
        'network': """config device 'bridge'
 option name 'br-lan'
 option type 'bridge'
 list ports 'eth0'
 list ports 'eth2.100'
 list ports 'phy0-ap0'
config bridge-vlan 'tags'
 option device 'br-lan'
 list ports 'eth1:t'
 list ports 'eth4:u*'
config device 'tagged'
 option name 'pon-data0.100.200'
 option type '8021q'
 option ifname 'pon-data0.100'
 option vid '200'
config interface 'wan'
 option device 'pon-data0.41'
 option proto 'pppoe'
 option username 'eth0 pon-data0.41'
 option password 'keep eth4 and pon-data0 unchanged here'
config interface 'wan6'
 option device '@wan'
 option proto 'dhcpv6'
config interface 'legacy'
 option ifname 'eth0 eth3 pon-data7.45'
config interface 'other'
 option device 'myeth0'
config interface 'othernumber'
 option device 'eth40'
config interface 'last_llid'
 option device 'pon-data31'
config interface 'omci'
 option device 'pon-omci'
config interface 'oam'
 option device 'pon-oam0'
config route 'logical'
 option interface 'eth0'
""",
        'system': """config system
 option hostname 'eth0'
config led 'port'
 option dev 'eth4'
 option name 'eth4'
config led 'internet'
 option dev 'pppoe-wan'
""",
        'firewall': """config zone 'wan'
 list device 'pon-data0.41'
 list device '!eth3.100'
 list device 'custom0'
 list network 'eth0'
""",
        'wireless': """config wifi-iface 'ap'
 option ifname 'phy0-ap0'
 option ssid 'eth0 pon-data0'
 option key 'leave eth4 alone'
""",
        'sqm': """config queue 'wan'
 option interface 'pon-data0.41'
""",
        'pon': """config pon 'pon'
 option loid 'eth0 pon-data0'
""",
    }
    for name, text in files.items():
        (config / name).write_text(text)
    script = work / 'rename.uc'
    script.write_text(source.read_text().replace('cursor()',
        'cursor(' + json.dumps(str(config)) + ', ' + json.dumps(str(stage)) + ')'))
    cli = command() + ['-c', str(config), '-t', str(stage)]

    def get(key):
        return subprocess.check_output(cli + ['get', key], text=True).strip()

    subprocess.run(runtime + [str(script)], check=True)
    expected = {
        'network.bridge.ports': 'lan1 lan3.100 phy0-ap0',
        'network.tags.ports': 'lan2:t lan5:u*',
        'network.tagged.name': 'pon0.100.200',
        'network.tagged.ifname': 'pon0.100',
        'network.wan.device': 'pon0.41',
        'network.wan6.device': '@wan',
        'network.legacy.ifname': 'lan1 lan4 pon7.45',
        'network.other.device': 'myeth0',
        'network.othernumber.device': 'eth40',
        'network.last_llid.device': 'pon31',
        'network.omci.device': 'pon-omci',
        'network.oam.device': 'pon-oam0',
        'network.logical.interface': 'eth0',
        'network.wan.username': 'eth0 pon-data0.41',
        'network.wan.password': 'keep eth4 and pon-data0 unchanged here',
        'system.port.dev': 'lan5',
        'system.port.name': 'eth4',
        'system.internet.dev': 'pppoe-wan',
        'system.@system[0].hostname': 'eth0',
        'system.@system[0].tyfza1_netdev_names': '1',
        'firewall.wan.device': 'pon0.41 !lan4.100 custom0',
        'firewall.wan.network': 'eth0',
        'wireless.ap.ssid': 'eth0 pon-data0',
        'wireless.ap.key': 'leave eth4 alone',
        'sqm.wan.interface': 'pon0.41',
        'pon.pon.loid': 'eth0 pon-data0',
    }
    for key, value in expected.items():
        assert get(key) == value, (key, get(key), value)
    exported = subprocess.check_output(cli + ['export', 'network'], text=True)
    assert "list ports 'lan1'" in exported and "option ifname 'lan1 lan4 pon7.45'" in exported
    assert 'ucode-mod-uci' in (root / 'port/overlay/target/linux/rtl9617c/Makefile').read_text()

    # Repeated upgrades must not reinterpret a newly attached USB NIC as old LAN1.
    subprocess.run(cli + ['set', 'network.other.device=eth0'], check=True)
    subprocess.run(cli + ['commit', 'network'], check=True)
    before = {p.name: p.read_bytes() for p in config.iterdir()}
    subprocess.run(runtime + [str(script)], check=True)
    assert before == {p.name: p.read_bytes() for p in config.iterdir()}

print('Native device migration: VLAN/QinQ, custom bridge membership, firewall, LED and SQM references passed')
print('Credentials, logical interfaces and Wi-Fi settings preserved; repeated upgrade and new USB eth0 left intact')
