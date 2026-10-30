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
    board = work / 'board_name'
    board.write_text('tianyi,tyfza1-2p5g\n')
    script = work / 'rename.uc'
    script.write_text(source.read_text().replace('cursor()',
        'cursor(' + json.dumps(str(config)) + ', ' + json.dumps(str(stage)) + ')')
        .replace('/tmp/sysinfo/board_name', str(board)))
    cli = command() + ['-c', str(config), '-t', str(stage)]

    def get(key):
        return subprocess.check_output(cli + ['get', key], text=True).strip()

    subprocess.run(runtime + [str(script)], check=True)
    expected = {
        'network.bridge.ports': 'lan4 lan2.100 phy0-ap0',
        'network.tags.ports': 'lan3:t lan1:u*',
        'network.tagged.name': 'pon0.100.200',
        'network.tagged.ifname': 'pon0.100',
        'network.wan.device': 'pon0.41',
        'network.wan6.device': '@wan',
        'network.legacy.ifname': 'lan4 eth3 pon7.45',
        'network.other.device': 'myeth0',
        'network.othernumber.device': 'eth40',
        'network.last_llid.device': 'pon31',
        'network.omci.device': 'pon-omci',
        'network.oam.device': 'pon-oam0',
        'network.logical.interface': 'eth0',
        'network.wan.username': 'eth0 pon-data0.41',
        'network.wan.password': 'keep eth4 and pon-data0 unchanged here',
        'system.port.dev': 'lan1',
        'system.port.name': 'eth4',
        'system.internet.dev': 'pppoe-wan',
        'system.@system[0].hostname': 'eth0',
        'system.@system[0].tyfza1_netdev_names': '2',
        'firewall.wan.device': 'pon0.41 !eth3.100 custom0',
        'firewall.wan.network': 'eth0',
        'wireless.ap.ssid': 'eth0 pon-data0',
        'wireless.ap.key': 'leave eth4 alone',
        'sqm.wan.interface': 'pon0.41',
        'pon.pon.loid': 'eth0 pon-data0',
    }
    for key, value in expected.items():
        assert get(key) == value, (key, get(key), value)
    exported = subprocess.check_output(cli + ['export', 'network'], text=True)
    assert "list ports 'lan4'" in exported and "option ifname 'lan4 eth3 pon7.45'" in exported
    assert 'ucode-mod-uci' in (root / 'port/overlay/target/linux/rtl9617c/Makefile').read_text()

    # Repeated upgrades must not reinterpret a newly attached USB NIC as old LAN1.
    subprocess.run(cli + ['set', 'network.other.device=eth0'], check=True)
    subprocess.run(cli + ['commit', 'network'], check=True)
    before = {p.name: p.read_bytes() for p in config.iterdir()}
    subprocess.run(runtime + [str(script)], check=True)
    assert before == {p.name: p.read_bytes() for p in config.iterdir()}

    # Exercise the real upgrade boundaries, including already-migrated USB NICs.
    for board_name, old_version, expected_ports in (
        ('tianyi,tyfza1', None, 'lan4 lan3 lan2 lan1 eth4'),
        ('tianyi,tyfza1-2p5g', None, 'lan4 lan3 lan2 eth3 lan1'),
        ('tianyi,tyfza1', '1', 'eth0 eth1 eth2 eth3 eth4'),
        ('tianyi,tyfza1-2p5g', '1', 'eth0 eth1 eth2 eth3 eth4'),
    ):
        board.write_text(board_name + '\n')
        for path in config.iterdir():
            path.unlink()
        (config / 'system').write_text('config system main\n' +
            (f' option tyfza1_netdev_names {old_version}\n' if old_version else ''))
        (config / 'network').write_text("""config interface 'native'
 option device 'eth0 eth1 eth2 eth3 eth4'
config device 'bridge'
 option name 'br-lan'
 option type 'bridge'
 list ports 'lan1'
 list ports 'lan2'
 list ports 'lan3'
 list ports 'lan4'
 list ports 'lan5'
config bridge-vlan 'tags'
 option device 'br-lan'
 list ports 'lan1:u*'
 list ports 'lan5:u*'
config device 'qinq'
 option name 'lan5.100.200'
 option ifname 'lan5.100'
config interface 'keep'
 option device 'lan4'
config interface 'prefix'
 option device 'lan50 mylan5'
""")
        (config / 'firewall').write_text("config zone 'wan'\n list device '!lan5.100'\n")
        subprocess.run(runtime + [str(script)], check=True)
        is_2p5g = board_name.endswith('-2p5g')
        assert get('network.native.device') == expected_ports
        assert get('network.bridge.ports') == 'lan1 lan2 lan3 lan4' + ('' if is_2p5g else ' lan5')
        assert get('network.tags.ports') == ('lan1:u*' if is_2p5g else 'lan1:u* lan5:u*')
        assert get('network.qinq.name') == ('lan1.100.200' if is_2p5g else 'lan5.100.200')
        assert get('network.qinq.ifname') == ('lan1.100' if is_2p5g else 'lan5.100')
        assert get('firewall.wan.device') == ('!lan1.100' if is_2p5g else '!lan5.100')
        assert get('network.keep.device') == 'lan4'
        assert get('network.prefix.device') == 'lan50 mylan5'
        assert get('system.main.tyfza1_netdev_names') == '2'
        subprocess.run(cli + ['set', 'network.native.device=eth0 eth4 lan5'], check=True)
        subprocess.run(cli + ['commit', 'network'], check=True)
        before = {p.name: p.read_bytes() for p in config.iterdir()}
        subprocess.run(runtime + [str(script)], check=True)
        assert before == {p.name: p.read_bytes() for p in config.iterdir()}

print('Native device migration: VLAN/QinQ, custom bridge membership, firewall, LED and SQM references passed')
print('Credentials, logical interfaces and Wi-Fi settings preserved; repeated upgrade and new USB eth0 left intact')
print('Both boards: hardware port mapping, version-1 lan5 repair, deduplication and version-2 idempotence passed')
