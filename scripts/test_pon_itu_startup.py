#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise ITU startup without EPON identity in an isolated OpenWrt root."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('runtime', type=Path, help='Isolated root with ucode, uci and pon-identity')
args = parser.parse_args()
runtime = args.runtime.resolve()
project = Path(__file__).resolve().parents[1]
importer = project / 'port/packages/tyfza1-pon-control/files/factory.uc'
service = project / 'port/overlay/target/linux/rtl9617c/base-files/etc/init.d/pon'
shutil.copyfile(importer, runtime / 'factory.uc')
(runtime / 'pon-service.sh').write_text(service.read_text().replace(
    '/sys/bus/platform/devices/4f5500000.pon', '/device'))
for directory in ('input', 'output', 'etc/config', 'var/run/tyfza1-factory', 'dev', 'tmp'):
    (runtime / directory).mkdir(parents=True, exist_ok=True)
(runtime / 'dev/null').touch()
(runtime / 'bin/cat').unlink(missing_ok=True)
(runtime / 'bin/cat').symlink_to('busybox')


def run(*command):
    return subprocess.run(['sudo', '-n', 'chroot', f'--userspec={os.getuid()}:{os.getgid()}',
                           str(runtime), *command],
                          text=True, capture_output=True)


def import_itu(case, epon):
    source = runtime / 'input'
    (source / 'config_hs.xml').write_text('<Config>'
        '<Value Name="GPON_SN" Value="TEST12345678"/>'
        '<Value Name="GPON_ONU_MODEL" Value="TYFZA1"/></Config>\n')
    (source / 'config.xml').write_text('<Config>' + epon +
        '<Value Name="GPON_PLOAM_PASSWD" Value="test-pass"/></Config>\n')
    (source / 'rtkbosa_k.bin').write_bytes(bytes(1024))
    destination = runtime / 'output' / case
    shutil.rmtree(destination, ignore_errors=True)
    result = run('/usr/bin/ucode', '/factory.uc', '/input', '/output/' + case)
    # The EPON import error remains visible; ITU identity is still usable.
    assert result.returncode == 1 and 'Factory EPON identity:' in result.stderr, result
    identity = destination / 'var/run/tyfza1-factory/pon_factory'
    text = identity.read_text()
    assert "serial_number 'TEST12345678'" in text and 'option onu_mac' not in text
    shutil.copyfile(identity, runtime / 'var/run/tyfza1-factory/pon_factory')


launcher = r'''
PATH=/sbin:/usr/sbin:/bin:/usr/bin
config_load() { :; }
config_get() {
    local value
    value=$(uci -q get "pon.$2.$3")
    export "$1=${value:-$4}"
}
config_get_bool() { config_get "$@"; }
ip() { :; }
procd_open_instance() { :; }
procd_set_param() { :; }
procd_close_instance() { :; }
logger() {
    if [ "$#" -gt 2 ]; then
        echo "$*" >> /tmp/pon-log
    else
        cat >> /tmp/pon-log
    fi
}
. /pon-service.sh
start_service
'''


def start(mode='xgpon', custom=''):
    device = runtime / 'device'
    shutil.rmtree(device)
    device.mkdir()
    for attribute in ('bosa', 'serdes', 'mac', 'puc', 'pdc', 'gpon_identity',
                      'xgpon_identity', 'activation', 'fe'):
        (device / attribute).touch()
    # Any attempted access to the unrelated EPON MAC endpoint must fail.
    if not custom:
        (device / 'onu_mac').mkdir()
    (runtime / 'etc/config/pon').write_text("config pon 'pon'\n"
        f"\toption enabled '1'\n\toption mode '{mode}'\n" + custom)
    (runtime / 'tmp/pon-log').write_text('')
    return run('/bin/sh', '-c', launcher)


for case, epon in (
    ('missing-epon', ''),
    ('zero-epon-mac', '<chain chainName="EPON_LLID_TBL">'
                      '<Value Name="macAddr" Value="000000000000"/></chain>'),
):
    import_itu(case, epon)
    for mode in ('gpon', 'xgpon', 'xgspon'):
        result = start(mode)
        assert result.returncode == 0, (case, mode, result.stderr)
        attribute, length = ('gpon_identity', 10) if mode == 'gpon' else ('xgpon_identity', 36)
        identity = (runtime / 'device' / attribute).read_bytes()
        assert identity == b'TEST\x12\x34\x56\x78' + b'test-pass'.ljust(length, b'\0')
        assert (runtime / 'device/activation').read_text() == '1\n'
    print(case + ': original ITU identity reaches GPON/XG-PON/XGS-PON activation')

# A configured Ethernet MAC remains applied on ITU links.
result = start(custom="\toption onu_mac '02:11:22:33:44:55'\n")
assert result.returncode == 0, result.stderr
assert (runtime / 'device/onu_mac').read_text() == '02:11:22:33:44:55\n'
print('Explicit ITU Ethernet MAC is preserved')

# Invalid SN must stop before MAC programming, retaining its actual error.
result = start(custom="\toption serial_number 'invalid'\n")
assert result.returncode == 1 and 'PON serial_number' in result.stderr, result
assert 'stage=identity error=1' in (runtime / 'var/run/pon-service').read_text()
assert 'PON serial_number' in (runtime / 'var/run/pon-service-error').read_text()
assert 'PON serial_number' in (runtime / 'tmp/pon-log').read_text()
assert (runtime / 'device/mac').read_bytes() == b''
print('Invalid identity stops startup; failing step and original error remain visible')

result = start('epon-d10g')
assert result.returncode != 0
assert 'stage=onu_mac error=' in (runtime / 'var/run/pon-service').read_text()
assert (runtime / 'device/mac').read_bytes() == b''
print('EPON still requires its LLID MAC before MAC preparation')

# The inverse case: missing ITU identity must not discard valid EPON credentials.
(runtime / 'input/config_hs.xml').write_text('<Config>'
    '<Value Name="PON_VENDOR_ID" Value="TEST"/>'
    '<Value Name="EPON_ONU_MODEL" Value="FZA1"/></Config>\n')
(runtime / 'input/config.xml').write_text('<Config><chain chainName="EPON_LLID_TBL">'
    '<Value Name="macAddr" Value="021122334455"/></chain>'
    '<Value Name="LOID" Value="test-loid"/></Config>\n')
result = run('/usr/bin/ucode', '/factory.uc', '/input', '/output/no-itu')
assert result.returncode == 1 and 'Factory ITU identity:' in result.stderr, result
identity = runtime / 'output/no-itu/var/run/tyfza1-factory/pon_factory'
text = identity.read_text()
assert "onu_mac '02:11:22:33:44:55'" in text and "loid 'test-loid'" in text
assert 'option serial_number' not in text
shutil.copyfile(identity, runtime / 'var/run/tyfza1-factory/pon_factory')
(runtime / 'etc/config/pon').write_text("config pon 'pon'\n\toption mode 'epon-d10g'\n")
result = run('/usr/sbin/pon-oamd', '--check-config')
assert result.returncode == 0 and '"loid_configured":true' in result.stdout, result
print('Missing GPON SN: actual EPON daemon loads the original MAC/LOID identity')

# Stop the running family even when UCI already selects the other family.
for family, mode in (('epon', 'gpon'), ('gpon', 'epon-d10g')):
    device = runtime / 'device'
    shutil.rmtree(device)
    device.mkdir()
    (device / 'state').write_text('family=' + family + ' control=0\n')
    (device / 'serdes').write_text('mode=' + ('gpon' if family == 'gpon' else 'epon-d10g') + ' configured=1\n')
    (runtime / 'etc/config/pon').write_text("config pon 'pon'\n\toption mode '" + mode + "'\n")
    owned = ('traffic', 'mpcp') if family == 'epon' else ('activation', 'fe')
    for attribute in ('traffic', 'mpcp', 'activation', 'fe'):
        path = device / attribute
        if attribute in owned:
            path.write_text('1\n')
        else:
            path.mkdir()
    for interface in ('pon-oam0', 'pon0'):
        (runtime / 'sys/class/net' / interface).mkdir(parents=True, exist_ok=True)
    (runtime / 'tmp/stop-interfaces').write_text('')
    stop = launcher.replace('ip() { :; }', 'ip() { echo "$*" >> /tmp/stop-interfaces; }')
    result = run('/bin/sh', '-c', stop.replace('\nstart_service\n', '\nstop_service\n'))
    assert result.returncode == 0 and not result.stderr, (family, result)
    assert all((device / name).read_text() == '0\n' for name in owned)
    interfaces = (runtime / 'tmp/stop-interfaces').read_text()
    assert 'pon0 down' in interfaces
    assert ('pon-omci down' in interfaces) == (family == 'gpon')
    assert ('pon-oam0 down' in interfaces) == (family == 'epon')
print('Family switch: stop follows the live driver family, with separate OAM/OMCI control')

# A failed shared-FE cleanup remains an error while MAC/data shutdown proceeds.
(runtime / 'device/fe').unlink()
(runtime / 'device/fe').mkdir()
result = run('/bin/sh', '-c', stop.replace('\nstart_service\n', '\nstop_service\n'))
assert result.returncode != 0 and 'fe' in result.stderr
assert (runtime / 'device/activation').read_text() == '0\n'
assert 'stage=stop error=1' in (runtime / 'var/run/pon-service').read_text()
print('Failed ITU FE cleanup is reported without skipping MAC shutdown')
