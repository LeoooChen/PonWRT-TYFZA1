#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the actual factory importer with synthetic per-unit inputs in an OpenWrt root."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('runtime', type=Path, help='Isolated OpenWrt root with ucode and its modules')
args = parser.parse_args()
project = Path(__file__).resolve().parents[1]
runtime = args.runtime.resolve()
shutil.copy2(project / 'port/packages/tyfza1-pon-control/files/factory.uc', runtime / 'factory.uc')
source = runtime / 'input'
source.mkdir(exist_ok=True)
fields = {'ELAN_MAC_ADDR': '0011223344ff', 'WLAN_MAC_ADDR': '00e04c000001', 'GPON_SN': 'TEST12345678',
          'PON_VENDOR_ID': 'TEST', 'EPON_ONU_MODEL': 'TYFZA1', 'PON_MODE': '2', 'PON_SPEED': '1'}
for band in ('2G_CCK', '2G_OFDM', '5GL', '5GM', '5GH'):
    fields['HW_RX_GAINGAP_' + band + '_AB'] = '3'
for radio in (0, 1):
    for chain in ('A', 'B'):
        fields[f'HW_THERMAL_{chain}_WLAN{radio}'] = str(20 + radio)
    fields[f'HW_XCAP_WLAN{radio}_AX'] = str(30 + radio)
for chain in ('A', 'B'):
    for band, count in (('2G_CCK', 6), ('2G_BW40_1S', 5), ('5G_BW40_1S', 14)):
        fields[f'HW_{band}_TSSI_{chain}'] = ','.join(f'{n + 1:02x}' for n in range(count))
    for kind in ('GAIN', 'CW'):
        for band, count in (('2G_CCK', 6), ('2G', 5), ('5G', 14)):
            fields[f'HW_{band}_TSSI_{kind}_DIFF_{chain}'] = ','.join(f'{n + 2:02x}' for n in range(count))

def invoke(script, case, values, bosa, identity=True, reset=True):
    (source / 'config_hs.xml').write_text('<Config>\n' + '\n'.join(
        f'<Value Name="{key}" Value="{value}"/>' for key, value in values.items()) + '\n</Config>\n')
    config = source / 'config.xml'
    if isinstance(identity, str):
        config.write_text(identity)
    elif identity:
        config.write_text('<Config><chain chainName="EPON_LLID_TBL">'
            '<Value Name="macAddr" Value="021122334456"/></chain></Config>\n')
    else:
        config.unlink(missing_ok=True)
    optical = source / 'rtkbosa_k.bin'
    if bosa is None:
        optical.unlink(missing_ok=True)
    else:
        optical.write_bytes(bosa)
    destination = runtime / 'output' / case
    if reset and destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['sudo', '-n', 'chroot', f'--userspec={os.getuid()}:{os.getgid()}',
        str(runtime), '/usr/bin/ucode', '/' + script, '/input', '/output/' + case],
        text=True, capture_output=True)
    radios = [destination / f'tmp/tyfza1-factory/tyfza1/{name}/flash-calibration.bin'
              for name in ('rtl8852ce', 'rtl8192xbe')]
    data = [path.read_bytes() if path.exists() else None for path in radios]
    return result, data, destination

valid_bosa = bytes(range(256)) * 4
normal, expected, destination = invoke('factory.uc', 'normal', fields, valid_bosa)
assert normal.returncode == 0, normal.stderr
assert all(value and len(value) == 284 and value[:8] == b'TYFC0003' for value in expected)
assert expected[0] != expected[1]  # Each radio retains its own thermal/crystal inputs.
assert expected[0][66:72] == bytes.fromhex('001122334501')
assert expected[1][66:72] == bytes.fromhex('001122334500')
assert (destination / 'tmp/tyfza1-factory/lan_mac').read_text() == '00:11:22:33:44:ff\n'
for family, speed, mode in (('1', '0', 'gpon'), ('1', '1', 'xgpon'),
                            ('1', '2', 'xgspon'), ('2', '0', 'epon'),
                            ('2', '1', 'epon-d10g'), ('2', '2', 'epon-bi10g')):
    values = dict(fields, PON_MODE=family, PON_SPEED=speed, PON_SUB_MODE='0')
    result, data, imported = invoke('factory.uc', 'mode-' + mode, values, valid_bosa)
    assert result.returncode == 0 and data == expected, result.stderr
    assert f"option mode '{mode}'" in (imported / 'var/run/tyfza1-factory/pon_factory').read_text()
print('Factory protocol: all six OEM family/speed mappings imported')

# Stock PON_MODE=0 selects Active Ethernet, independently of PON_SPEED.
# It must not abort storage migration or invent a GPON/EPON protocol.
for speed in ('0', '1', '2'):
    values = dict(fields, PON_MODE='0', PON_SPEED=speed)
    for suffix, config in (('epon', True), ('empty', '<Config/>')):
        result, data, imported = invoke('factory.uc', f'active-ethernet-{speed}-{suffix}',
                                        values, valid_bosa, config)
        assert result.returncode == 0 and data == expected, result.stderr
        assert 'Active Ethernet' in result.stderr
        identity_text = (imported / 'var/run/tyfza1-factory/pon_factory').read_text()
        assert 'option mode ' not in identity_text
        assert "option serial_number 'TEST12345678'" in identity_text
        assert (imported / 'tmp/tyfza1-factory/rtl9617c/bosa-calibration.bin').read_bytes() == valid_bosa
        assert (imported / 'tmp/tyfza1-factory/lan_mac').read_text() == '00:11:22:33:44:ff\n'
        if config is True:
            assert "option onu_mac '02:11:22:33:44:56'" in identity_text
for family, speed in (('3', '1'), ('1', '9'), ('2', '9')):
    result, _, _ = invoke('factory.uc', f'unknown-mode-{family}-{speed}',
                           dict(fields, PON_MODE=family, PON_SPEED=speed, PON_SUB_MODE='0'), valid_bosa)
    assert result.returncode != 0 and 'Unsupported factory PON mode/speed' in result.stderr
result, _, _ = invoke('factory.uc', 'active-ethernet-bad-bosa',
                       dict(fields, PON_MODE='0'), b'bad')
assert result.returncode != 0 and 'Factory optical:' in result.stderr
for case, config in (('missing', False), ('empty', '')):
    result, data, _ = invoke('factory.uc', 'active-ethernet-' + case + '-config',
                              dict(fields, PON_MODE='0'), valid_bosa, config)
    assert result.returncode != 0 and data == expected, result.stderr
    assert 'Factory PON configuration:' in result.stderr
print('Active Ethernet imports calibration and identity without selecting a PON mode; invalid inputs still fail')

# The 2.5G factory XG-PON configuration has no EPON LLID MAC.
for family, speed, mode in (('1', '0', 'gpon'), ('1', '1', 'xgpon'), ('1', '2', 'xgspon'), ('2', '1', 'epon-d10g')):
    values = dict(fields, PON_MODE=family, PON_SPEED=speed, PON_SUB_MODE='0')
    for config in ('<Config><chain chainName="EPON_LLID_TBL"></chain></Config>', '<Config/>'):
        result, data, imported = invoke('factory.uc', 'empty-epon-' + mode, values, valid_bosa, config)
        assert result.returncode == (1 if family == '2' else 0), result.stderr
        assert data == expected
        identity_text = (imported / 'var/run/tyfza1-factory/pon_factory').read_text()
        assert "option serial_number 'TEST12345678'" in identity_text
        assert 'option onu_mac' not in identity_text
bad_mac = '<Config><chain chainName="EPON_LLID_TBL"><Value Name="macAddr" Value="bad"/></chain></Config>'
result, _, _ = invoke('factory.uc', 'invalid-epon-mac', dict(fields, PON_MODE='1', PON_SUB_MODE='0'), valid_bosa, bad_mac)
assert result.returncode != 0
print('ITU empty EPON table accepted; EPON missing identity and malformed supplied MAC rejected')

for case, bosa, identity in (('short-bosa', b'bad', True), ('extended-bosa', valid_bosa + b'original suffix', True),
                             ('missing-bosa', None, True), ('missing-pon-config', valid_bosa, False)):
    result, data, destination = invoke('factory.uc', case, fields, bosa, identity)
    valid_optical = bosa is not None and len(bosa) >= 1024
    assert result.returncode == (0 if valid_optical and identity else 1) and data == expected, (case, result.stderr)
    optical = destination / 'tmp/tyfza1-factory/rtl9617c/bosa-calibration.bin'
    if valid_optical:
        assert optical.read_bytes() == bosa
    else:
        assert not optical.exists()
    assert not list(destination.rglob('*.new'))
    print(f'{case}: both original-format radio calibrations generated')

modified = dict(fields)
del modified['HW_THERMAL_A_WLAN0']
result, data, destination = invoke('factory.uc', 'radio0-field-missing', modified, valid_bosa)
assert result.returncode != 0 and data == [None, expected[1]]
assert (destination / 'tmp/tyfza1-factory/rtl9617c/bosa-calibration.bin').read_bytes() == valid_bosa
assert (destination / 'var/run/tyfza1-factory/pon_factory').exists()
print('radio0-field-missing: other radio, optical calibration and PON identity imported')

for failed_bosa in (None, b'bad'):
    invoke('factory.uc', 'repeat-import', fields, valid_bosa)
    result, data, destination = invoke('factory.uc', 'repeat-import', modified, failed_bosa, reset=False)
    assert result.returncode != 0 and data == [None, expected[1]]
    assert not (destination / 'tmp/tyfza1-factory/rtl9617c/bosa-calibration.bin').exists()
print('Repeated import: missing/short optical data and invalid radio data revoke the old calibration')

# Run the production boot service with an isolated root and modeled UBI mounts.
service = runtime / 'etc/init.d/tyfza1-factory'
service.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(project / 'port/overlay/target/linux/rtl9617c/base-files/etc/init.d/tyfza1-factory', service)
importer = runtime / 'usr/share/tyfza1/factory.uc'
importer.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(project / 'port/packages/tyfza1-pon-control/files/factory.uc', importer)
importer.chmod(0o755)
shutil.copy2(project / 'port/packages/tyfza1-storage/files/tyfza1-factory.sh',
             runtime / 'lib/tyfza1-factory.sh')
(runtime / 'sys/class/ubi/ubi0').mkdir(parents=True, exist_ok=True)
pon_mac = runtime / 'sys/bus/platform/devices/4f5500000.pon/data_mac'
pon_mac.parent.mkdir(parents=True, exist_ok=True)
pon_mac.write_text('00:00:00:00:00:00\n')
for port in range(1, 6):
    (runtime / f'sys/class/net/lan{port}').mkdir(parents=True, exist_ok=True)
(runtime / 'dev').mkdir(exist_ok=True)
invoke('factory.uc', 'boot-input', fields, valid_bosa)
calibrations = ['rtl9617c/bosa-calibration.bin'] + [
    f'tyfza1/{name}/flash-calibration.bin' for name in ('rtl8852ce', 'rtl8192xbe')]
# Run the package's actual link installation recipe before boot. The links
# must belong to the image; boot may only write the per-unit data in tmpfs.
package = (project / 'port/packages/tyfza1-pon-control/Makefile').read_text()
install = package.split('define Package/tyfza1-pon-control/install\n', 1)[1].split('endef', 1)[0]
link_recipe = ''.join(line + '\n' for line in install.splitlines() if '/lib/firmware/' in line)
assert link_recipe, 'Package must install the calibration links'
recipe = runtime / 'calibration-links.mk'
recipe.write_text('INSTALL_DIR:=mkdir -p\nLN:=ln -sf\ndefine install\n' + link_recipe +
                  'endef\n.PHONY: install\ninstall:\n\t$(call install,' + str(runtime) + ')\n')
subprocess.run(['make', '--no-print-directory', '-f', str(recipe), 'install'], check=True)
for calibration in calibrations:
    link = runtime / 'lib/firmware/realtek' / calibration
    assert link.is_symlink() and os.readlink(link) == '/tmp/tyfza1-factory/' + calibration

def boot(mount_ok, readonly=False):
    script = ('. /etc/init.d/tyfza1-factory\n'
              'mount() { ' + ('cp /input/* "$6"/;' if mount_ok else 'return 5;') + ' }\n'
              'umount() { :; }\n'
              'ip() { echo "$*" >> /tmp/factory-lan-mac.log; }\n'
              ': > /tmp/factory-lan-mac.log\nstart\n')
    command = ['chroot', f'--userspec={os.getuid()}:{os.getgid()}',
               str(runtime), '/bin/sh', '-c', script]
    if readonly:
        command = ['unshare', '--mount', '--propagation', 'private', '/bin/sh', '-c',
                   'mount --bind "$1/lib/firmware" "$1/lib/firmware" && '
                   'mount -o remount,bind,ro "$1/lib/firmware" && '
                   'exec chroot --userspec="$2" "$1" /bin/sh -c "$3"',
                   'factory-test', str(runtime), f'{os.getuid()}:{os.getgid()}', script]
    return subprocess.run(['sudo', '-n', *command], text=True, capture_output=True)

result = boot(True, readonly=True)
assert result.returncode == 0, (result.stdout, result.stderr)
assert pon_mac.read_text() == '00:11:22:33:44:ff\n'
assert (runtime / 'tmp/factory-lan-mac.log').read_text().splitlines() == [
    f'link set dev lan{port} address 00:11:22:33:44:ff' for port in range(1, 6)]
for calibration, data in zip(calibrations, [valid_bosa] + expected):
    link = runtime / 'lib/firmware/realtek' / calibration
    assert link.is_symlink() and os.readlink(link) == '/tmp/tyfza1-factory/' + calibration
    assert (runtime / 'tmp/tyfza1-factory' / calibration).read_bytes() == data
invoke('factory.uc', 'boot-active-ethernet', dict(fields, PON_MODE='0'), valid_bosa)
result = boot(True, readonly=True)
assert result.returncode == 0, (result.stdout, result.stderr)
assert 'option mode ' not in (runtime / 'var/run/tyfza1-factory/pon_factory').read_text()
for calibration, data in zip(calibrations, [valid_bosa] + expected):
    assert (runtime / 'tmp/tyfza1-factory' / calibration).read_bytes() == data
print('Boot service: Active Ethernet succeeds and retains both radios and optical calibration')
result = boot(False)
assert result.returncode == 5, (result.stdout, result.stderr)
assert not (runtime / 'tmp/run/tyfza1-factory/pon_factory').exists()
for calibration in calibrations:
    result = subprocess.run(['sudo', '-n', 'chroot', f'--userspec={os.getuid()}:{os.getgid()}',
        str(runtime), '/bin/cat', '/lib/firmware/realtek/' + calibration], capture_output=True)
    assert result.returncode != 0 and not result.stdout
print('Boot service: package-owned links work on a read-only firmware directory; mount failure revokes calibration')
print('Factory importer: original bytes, subsystem isolation and stale calibration removal passed')
