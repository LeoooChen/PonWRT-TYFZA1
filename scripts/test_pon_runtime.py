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
    profiles = [stock / f'etc/serdes/pon_serdes_{mode}.txt' for mode in modes]
    for profile in profiles:
        profile.write_text('0x4f5500010 0x12345678 0 0\n')
    output = temporary / 'clean-tree/files'
    command = [sys.executable, str(root / 'tools/prepare_pon_runtime.py'), str(output),
               '--stock', str(stock), '--manifest', str(temporary / 'manifest.json')]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    assert (output / 'etc/config/pon').stat().st_mode & 0o777 == 0o600
    assert (output / 'etc/config/pon').read_bytes() == (root / 'port/overlay/target/linux/rtl9617c/base-files/etc/config/pon').read_bytes()
    assert not (output / 'lib/firmware/realtek/rtl9617c/bosa-calibration.bin').exists()
    for mode in modes:
        blob = (output / f'lib/firmware/realtek/rtl9617c/pon_serdes_{mode}.bin').read_bytes()
        assert struct.unpack('<4sIIIII', blob) == (b'TPON', 1, 1, 16, 0x12345678, 10)
    checks.append('public files tree gets six profiles without subscriber identity or per-device calibration')
    for missing in profiles:
        saved = missing.read_bytes()
        missing.unlink()
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode != 0 and str(missing) in result.stderr
        missing.write_bytes(saved)
    checks.append('missing SerDes inputs fail despite pre-existing runtime files')
    profiles[0].write_text('')
    assert subprocess.run(command, capture_output=True).returncode != 0
    checks.append('empty SerDes profile fails')
    # Invoke the documented entry itself: missing input fails before feeds/make.
    environment = dict(os.environ, TYFZA1_STOCK_INPUTS=str(temporary / 'missing-stock'))
    result = subprocess.run(['sh', str(root / 'port/scripts/build.sh'), str(temporary / 'clean-tree'), '2'],
                            env=environment, capture_output=True, text=True)
    assert result.returncode != 0 and 'Missing mandatory PON input' in result.stderr
    assert not (temporary / 'clean-tree/.config').exists()
    checks.append('documented full-build entry fails before make when mandatory board data is absent')
(root / 'logs/pon-runtime-tests.json').write_text(json.dumps({'passed': checks}, indent=2) + '\n')
print(f'{len(checks)} clean-tree and missing-input build preparation checks passed')
