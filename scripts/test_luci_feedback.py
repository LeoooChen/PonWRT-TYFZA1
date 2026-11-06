#!/usr/bin/env python3
"""Apply the full LuCI series twice and execute scan ordering/capability guards."""
from pathlib import Path
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[1]
tree = Path(sys.argv[1]) if len(sys.argv)>1 else root / 'sources/openwrt'
feed = tree / 'feeds/luci'
with tempfile.TemporaryDirectory(prefix='luci-feedback-') as directory:
    work = Path(directory)
    luci = work / 'feeds/luci'
    luci.mkdir(parents=True)
    patches = sorted((root / 'port/luci-patches').glob('*.patch'))
    paths = {line[6:] for patch in patches for line in patch.read_text().splitlines()
             if line.startswith('--- a/')}
    for path in paths:
        dest = luci / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(['git', '-C', str(feed), 'show', 'HEAD:' + path]))
    subprocess.run(['git', 'init', '-q', str(luci)], check=True)
    for _ in range(2):
        subprocess.run(['sh', str(root / 'port/scripts/apply-luci-patches.sh'), str(work)], check=True)
    wireless = (luci / 'modules/luci-mod-network/htdocs/luci-static/resources/view/network/wireless.js').read_text()
    for path in paths:
        if path.endswith('.js'):
            subprocess.run(['node', '--check', str(luci / path)], check=True)
    start = wireless.index('results.sort(function(a, b) {')
    order = wireless[start:wireless.index('\n\t\t\t\tresults.forEach', start)]
    start = wireless.index('function isSingleRoleRadio(')
    guard = wireless[start:wireless.index('\n}', start) + 2]
    test = r'''
const assert = require('assert');
const uci = {get: (config, name, key) => name == 'builtin' ? 'soc/native' : 'usb/radio'};
const L = {hasSystemFeature: (feature, path) => path == 'soc/native' ? true : undefined};
'''
    test += guard + r'''
assert(isSingleRoleRadio('builtin'));
assert(!isSingleRoleRadio('usb'));
const results = [
 {signal:-70,quality:70,channel:1,bssid:'a'},
 {signal:-32,quality:70,channel:11,bssid:'b'},
 {signal:-40,quality:70,channel:6,bssid:'c'},
 {signal:0,quality:70,channel:2,bssid:'unknown-zero'},
 {signal:-256,quality:70,channel:3,bssid:'unknown-sentinel'},
 {quality:70,channel:4,bssid:'unknown-missing'},
 {signal:NaN,quality:70,channel:5,bssid:'unknown-nan'}
];
'''
    test += order + r'''
assert.deepEqual(results.map(r=>r.bssid), ['b','c','a','unknown-zero',
 'unknown-sentinel','unknown-missing','unknown-nan']);
'''
    subprocess.run(['node', '-e', test], check=True)
    assert "'disabled': isSingleRoleRadio" not in wireless
    for method in ['handleJoin', 'handleJoinConfirm']:
        chunk = wireless[wireless.index('s.' + method + ' ='):].split('\n\t\t};',1)[0]
        assert 'Wireless client mode is not supported' not in chunk
    assert "o.value('sta', _('Client'));\n\t\t\t\tif (singleRole)" in wireless
    assert "if (have_mesh) mode.value('mesh', '802.11s');" in wireless
    for mode in ('ap-wds', 'sta-wds'):
        assert "if (!singleRole) mode.value('" + mode + "'" in wireless
    assert "replace.default = '1';\n\t\t\t\treplace.readonly = true;" in wireless
    header = (luci / 'modules/luci-base/ucode/template/header.ut').read_text()
    assert '-tyfza1-r4' in header
    backend = (luci / 'modules/luci-base/root/usr/share/rpcd/ucode/luci').read_text()
    start = backend.index('result.wifi_single_role = {};')
    capability = backend[start:backend.index('const wifi_features', start)]
    ucode_test = r'''
import { basename } from 'fs';
let paths = {
 '/sys/class/ieee80211/phy0': '/sys/devices/platform/soc/4f0000000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0',
 '/sys/class/ieee80211/phy1': '/sys/devices/pci0001:00/0001:01:00.0',
 '/sys/class/ieee80211/phy2': '/sys/devices/platform/soc/usb/1-1',
 '/sys/class/ieee80211/phy3': null,
 '/sys/class/ieee80211/phy4': '/outside/devices/radio'
};
function glob(pattern) { return keys(paths); }
function readlink(path) { return index(path, '/phy2/') >= 0 ? '/drivers/other' : '/drivers/rtl_ax_ap'; }
function realpath(path) { return paths[replace(path, /\/device$/, '')]; }
let result = {};
''' + capability + r'''
if (length(result.wifi_single_role) != 2 ||
    result.wifi_single_role['soc/4f0000000.pcie/pci0000:00/0000:00:00.0/0000:01:00.0'] != true ||
    result.wifi_single_role['pci0001:00/0001:01:00.0'] != true)
 die('Incorrect single-role radio paths\n');
'''
    subprocess.run([str(tree / 'staging_dir/hostpkg/bin/ucode'),
                    '-e', ucode_test], check=True)
    print('PASS: full patch series applies twice; JS syntax, saturated-quality RSSI sort, '
          'unknown signals, backend device paths, per-radio guard and resource cache version')
