#!/usr/bin/env python3
"""Reject the old bank0 reset pins in actual compiled PCIe DTBs."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from stock_dtb import cells, fdt_nodes
from verify_pcie_dtb import verify

stock = (root / 'evidence/wireless-study-20261008/evidence/stock/live-fit.dtb').read_bytes()
native = (root / 'artifacts/pcie-dtb/board.dtb').read_bytes()
result = verify(stock, native)
assert [row['native_pin'] for row in result] == [101, 102]
negative = []
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='pcie-gpio-') as directory:
    path = Path(directory) / 'old-bank.dtb'
    for index, wrong_pin in ((0, 5), (1, 6)):
        path.write_bytes(native)
        node = f'/soc/pcie@{0xf4325000 + index * 0x1000:x}'
        spec = cells(fdt_nodes(native)[node]['reset-gpios'])
        subprocess.run(['fdtput', '-t', 'x', str(path), node, 'reset-gpios',
                        hex(spec[0]), hex(wrong_pin), hex(spec[2])], check=True)
        try:
            verify(stock, path.read_bytes())
        except AssertionError as error:
            assert str(error).startswith(f'PCIe{index} reset must be bank3')
            negative.append({'controller': index, 'old_pin_rejected': wrong_pin})
        else:
            raise AssertionError('Old GPIO numbering accepted')
(root / 'logs/pcie-gpio-regression.json').write_text(json.dumps({
    'correct_native_pins': [101, 102], 'wrong_bank_cases_rejected': negative,
    'scope': 'actual compiled DTB binding; device remains offline',
}, indent=2) + '\n')
print('PCIe reset binding:101/102 verified;compiled DTBs with old5/6 pins rejected')
