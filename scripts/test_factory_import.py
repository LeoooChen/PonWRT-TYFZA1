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
fields = {'WLAN_MAC_ADDR': '021122334455', 'GPON_SN': 'TEST12345678',
          'PON_VENDOR_ID': 'TEST', 'EPON_ONU_MODEL': 'TYFZA1'}
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

def invoke(script, case, values, bosa, identity=True):
    (source / 'config_hs.xml').write_text('<Config>\n' + '\n'.join(
        f'<Value Name="{key}" Value="{value}"/>' for key, value in values.items()) + '\n</Config>\n')
    config = source / 'config.xml'
    if identity:
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
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['sudo', '-n', 'chroot', f'--userspec={os.getuid()}:{os.getgid()}',
        str(runtime), '/usr/bin/ucode', '/' + script, '/input', '/output/' + case],
        text=True, capture_output=True)
    radios = [destination / f'lib/firmware/realtek/tyfza1/{name}/flash-calibration.bin'
              for name in ('rtl8852ce', 'rtl8192xbe')]
    data = [path.read_bytes() if path.exists() else None for path in radios]
    return result, data, destination

valid_bosa = bytes(range(256)) * 4
normal, expected, _ = invoke('factory.uc', 'normal', fields, valid_bosa)
assert normal.returncode == 0, normal.stderr
assert all(value and len(value) == 284 and value[:8] == b'TYFC0003' for value in expected)
assert expected[0] != expected[1]  # Each radio retains its own thermal/crystal inputs.
if (runtime / 'factory-before.uc').exists():
    before, data, _ = invoke('factory-before.uc', 'before-normal', fields, valid_bosa)
    assert before.returncode == 0 and data == expected
    before, data, _ = invoke('factory-before.uc', 'before-short-bosa', fields, b'bad')
    assert before.returncode != 0 and data == [None, None]
    print('Before fix: short BOSA prevents both radio calibrations; reproduced')

for case, bosa, identity in (('short-bosa', b'bad', True), ('extended-bosa', valid_bosa + b'original suffix', True),
                             ('missing-bosa', None, True), ('missing-pon-config', valid_bosa, False)):
    result, data, destination = invoke('factory.uc', case, fields, bosa, identity)
    assert result.returncode == (0 if bosa is not None and identity else 1) and data == expected, (case, result.stderr)
    if bosa is not None:
        assert (destination / 'lib/firmware/realtek/rtl9617c/bosa-calibration.bin').read_bytes() == bosa
    assert not list(destination.rglob('*.new'))
    print(f'{case}: both original-format radio calibrations generated')

modified = dict(fields)
del modified['HW_THERMAL_A_WLAN0']
result, data, destination = invoke('factory.uc', 'radio0-field-missing', modified, valid_bosa)
assert result.returncode != 0 and data == [None, expected[1]]
assert (destination / 'lib/firmware/realtek/rtl9617c/bosa-calibration.bin').read_bytes() == valid_bosa
assert (destination / 'var/run/tyfza1-factory/pon_factory').exists()
print('radio0-field-missing: other radio, optical calibration and PON identity imported')

print('Factory importer: valid bytes unchanged, independent radio/optical/PON imports passed')
