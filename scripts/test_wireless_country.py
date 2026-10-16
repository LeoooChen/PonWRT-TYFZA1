#!/usr/bin/env python3
"""Run board defaults, upstream wifi detection and country migration with real UCI."""
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

with tempfile.TemporaryDirectory(prefix='wireless-country-') as directory:
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
    # Redirect filesystem/CLI boundaries, retaining the actual OpenWrt shell helpers.
    uci_sh = work / 'uci.sh'
    uci_sh.write_text((tree / 'package/system/uci/files/lib/config/uci.sh').read_text()
                      .replace('/sbin/uci', str(wrapper)))
    functions = work / 'functions.sh'
    functions.write_text((tree / 'package/base-files/files/lib/functions.sh').read_text()
                         .replace('/lib/config/uci.sh', str(uci_sh)) + '\nLOAD_STATE=\n')
    defaults = work / 'uci-defaults.sh'
    defaults.write_text((tree / 'package/base-files/files/lib/functions/uci-defaults.sh').read_text()
                        .replace('/lib/functions.sh', str(functions))
                        .replace('/usr/share/libubox/jshn.sh', str(tree / 'staging_dir/host/share/libubox/jshn.sh')))
    board = work / 'board.json'
    board.write_text(json.dumps({'model': {'id': 'tyfza1'}, 'network': {'keep': True}}))
    board_script = work / 'board.sh'
    board_script.write_text((base / 'etc/board.d/03_wireless').read_text()
                            .replace('/lib/functions/uci-defaults.sh', str(defaults)))
    subprocess.run(shell + [str(board_script)], env=dict(env, CFG=str(board)), check=True)
    data = json.loads(board.read_text())
    assert data['wlan']['defaults'] == {'country': 'CN'}
    assert data['network'] == {'keep': True}
    for phy, band, width in [('phy0', '5G', 160), ('phy1', '2G', 20)]:
        data['wlan'][phy] = {'path': 'platform/' + phy, 'info': {'bands': {
            band: {'ht': True, 'he': band == '5G', 'max_width': width,
                   'default_channel': 36 if band == '5G' else 1}}}}
        sysfs = work / 'sys/class/ieee80211' / phy
        sysfs.mkdir(parents=True)
        (sysfs / 'macaddress').write_text('02:00:00:00:00:0' + phy[-1] + '\n')
    board.write_text(json.dumps(data))
    detector = work / 'mac80211.uc'
    detector.write_text((tree / 'package/network/config/wifi-scripts/files/lib/wifi/mac80211.uc').read_text()
                        .replace('"/etc/board.json"', json.dumps(str(board)))
                        .replace('uci.cursor()', 'uci.cursor(' + json.dumps(str(config)) + ')')
                        .replace('/sys/class/ieee80211/', str(work / 'sys/class/ieee80211') + '/'))
    ucode, = tree.glob('build_dir/target-*/ucode-*/ucode')
    detect = command()[:-1] + [str(ucode), '-L', str(ucode.parent / '*.so'), str(detector)]

    def get(key):
        return subprocess.check_output(cli + ['get', 'wireless.' + key], text=True).strip()

    generated = subprocess.check_output(detect, text=True)
    (config / 'wireless').touch()
    subprocess.run(cli + ['batch'], input=generated, text=True, check=True)
    for radio in ('radio0', 'radio1'):
        assert get(radio + '.country') == 'CN'
        assert get('default_' + radio + '.disabled') == '1'
        assert get('default_' + radio + '.ssid') == 'OpenWrt'
    assert get('radio0.htmode') == 'HE80' and get('radio0.channel') == '36'
    assert get('radio1.htmode') == 'HT20' and get('radio1.channel') == '1'

    migration = work / 'migrate.sh'
    migration.write_text((base / 'etc/uci-defaults/92-tyfza1-wireless-country').read_text()
                         .replace('/lib/functions.sh', str(functions))
                         .replace('/etc/config/wireless', str(config / 'wireless')))
    subprocess.run(cli + ['batch'], input="""
delete wireless.radio0.country
set wireless.radio0.htmode=HE160
set wireless.radio0.channel=44
set wireless.radio1.country=DE
set wireless.default_radio0.ssid=Private-AP
set wireless.default_radio0.encryption=psk2
set wireless.default_radio0.key=test-only-passphrase
set wireless.default_radio0.disabled=0
set wireless.radio2=wifi-device
set wireless.radio2.country=00
set wireless.radio3=wifi-device
set wireless.radio3.country=''
commit wireless
""", text=True, check=True)
    subprocess.run(shell + [str(migration)], env=env, check=True)
    assert get('radio0.country') == 'CN'
    assert get('radio3.country') == 'CN'
    assert get('radio1.country') == 'DE' and get('radio2.country') == '00'
    assert get('radio0.htmode') == 'HE160' and get('radio0.channel') == '44'
    assert get('default_radio0.ssid') == 'Private-AP'
    assert get('default_radio0.key') == 'test-only-passphrase'
    assert get('default_radio0.encryption') == 'psk2' and get('default_radio0.disabled') == '0'
    assert subprocess.check_output(detect, text=True) == ''
    before = (config / 'wireless').read_bytes()
    subprocess.run(shell + [str(migration)], env=env, check=True)
    assert (config / 'wireless').read_bytes() == before
    (config / 'wireless').unlink()
    subprocess.run(shell + [str(migration)], env=env, check=True)
    assert not (config / 'wireless').exists()
print('Wireless country: native board/UCI generation defaults to CN; migration preserves explicit countries and AP settings')
