#!/usr/bin/env python3
"""Check clean-tree PON preparation and failures without private board values."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
modes = ('epon', 'epon_d10g', 'epon_bi10g', 'gpon', 'xgpon', 'xgspon')
checks = []
with tempfile.TemporaryDirectory(prefix='runtime-test-', dir=root / 'artifacts') as temporary:
    temporary = Path(temporary)
    stock = temporary / 'stock'
    (stock / 'etc/serdes').mkdir(parents=True)
    (stock / 'var/config').mkdir(parents=True)
    profiles = [stock / f'etc/serdes/pon_serdes_{mode}.txt' for mode in modes]
    for profile in profiles:
        profile.write_text('0x4f5500010 0x12345678 0 0\n')
    calibration = stock / 'var/config/rtkbosa_k.bin'
    calibration.write_bytes(bytes(range(256)) * 4)
    config = stock / 'var/config/config.xml'
    config.write_text('<chain chainName="EPON_LLID_TBL"><Value Name="macAddr" Value="021122334455"/></chain>')
    scfg = stock / 'var/config/default_scfg.txt'
    scfg.write_text('CFG_ID_PON_LLID_NUM = 1\n')
    auth = temporary / 'auth.json'
    auth.write_text(json.dumps({'loid': '0012345678', 'password': '', 'network_protocol': 'dhcp'}))
    output = temporary / 'clean-tree/files'
    command = [sys.executable, str(root / 'tools/prepare_pon_runtime.py'), str(output),
               '--stock', str(stock), '--auth-config', str(auth), '--manifest', str(temporary / 'manifest.json')]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    assert (output / 'etc/config/pon').stat().st_mode & 0o777 == 0o600
    assert "option onu_mac '02:11:22:33:44:55'" in (output / 'etc/config/pon').read_text()
    assert (output / 'lib/firmware/realtek/rtl9617c/bosa-calibration.bin').read_bytes() == calibration.read_bytes()
    for mode in modes:
        blob = (output / f'lib/firmware/realtek/rtl9617c/pon_serdes_{mode}.bin').read_bytes()
        assert struct.unpack('<4sIIIII', blob) == (b'TPON', 1, 1, 16, 0x12345678, 10)
    checks.append('empty files tree gets all six profiles, exact BOSA calibration and private original-MAC config')
    for missing in profiles + [calibration, config, scfg, auth]:
        saved = missing.read_bytes()
        missing.unlink()
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode != 0 and str(missing) in result.stderr
        missing.write_bytes(saved)
    checks.append('all ten missing mandatory inputs fail despite pre-existing runtime files')
    profiles[0].write_text('')
    assert subprocess.run(command, capture_output=True).returncode != 0
    checks.append('empty SerDes profile fails')
    calibration.write_bytes(bytes(1023))
    assert subprocess.run(command, capture_output=True).returncode != 0
    checks.append('truncated BOSA calibration fails')
    # Invoke the documented entry itself: missing input fails before feeds/make.
    environment = dict(os.environ, TYFZA1_STOCK_INPUTS=str(temporary / 'missing-stock'), TYFZA1_AUTH_CONFIG=str(auth))
    result = subprocess.run(['sh', str(root / 'port/scripts/build.sh'), str(temporary / 'clean-tree'), '2'],
                            env=environment, capture_output=True, text=True)
    assert result.returncode != 0 and 'Missing mandatory PON input' in result.stderr
    assert not (temporary / 'clean-tree/.config').exists()
    checks.append('documented full-build entry fails before make when mandatory board data is absent')
(root / 'logs/pon-runtime-tests.json').write_text(json.dumps({'passed': checks}, indent=2) + '\n')
print(f'{len(checks)} clean-tree and missing-input build preparation checks passed')
