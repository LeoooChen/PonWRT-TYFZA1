#!/usr/bin/env python3
"""Verify native PCIe resources and banked reset GPIOs against stock DTB."""
import argparse
import hashlib
import json
from pathlib import Path
from stock_dtb import banked_gpio, cells, fdt_nodes, names, phandles

def verify(stock, native):
    original, target = fdt_nodes(stock), fdt_nodes(native)
    old_handles, new_handles = phandles(original), phandles(target)
    results = []
    for index in (0, 1):
        global_address = 0xf4325000 + index * 0x1000
        old = original[f'/pcie@{global_address:x}']
        new = target[f'/soc/pcie@{global_address:x}']
        assert names(new['compatible']) == ['realtek,rtl9617c-pcie']
        old_regs, new_regs = cells(old['reg']), cells(new['reg'])
        assert new_regs[:20] == old_regs, 'PCIe resource addresses differ from stock'
        atu = old_handles[cells(old['iommus'])[0]][1]
        assert new_regs[20:] == cells(atu['reg']), 'SoC coherent ATU differs from stock'
        reset_refs = cells(old['resets'])
        device_reset = old_handles[reset_refs[-1]][1]
        gpio = banked_gpio(old_handles, cells(device_reset['gpios']))
        new_gpio = cells(new['reset-gpios'])
        assert new_gpio[1:] == [gpio['native_pin'], gpio['flags']], \
            f"PCIe{index} reset must be bank{gpio['bank']}/pin{gpio['bank_pin']} = native{gpio['native_pin']}"
        native_gpio = new_handles[new_gpio[0]][1]
        assert names(native_gpio['compatible']) == ['realtek,rtl9617c-gpio']
        assert cells(native_gpio['reg']) == [4, 0xf43292c0, 0, 0xb4]
        assert cells(new['interrupts']) == cells(old['interrupts'])[:3]
        assert cells(new['ranges']) == cells(old['ranges'])
        assert cells(new['reset-delay-ms']) == [int.from_bytes(old['ready-time'], 'big')]
        for cut in 'AB':
            assert new['serdes-cfg-data' + cut] == old['serdes-cfg-data' + cut]
        results.append({'controller': index, 'stock_bank': gpio['bank'],
                        'stock_bank_pin': gpio['bank_pin'], 'native_pin': gpio['native_pin'],
                        'flags': gpio['flags'], 'resources_and_SerDes_match': True})
    return results

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('native', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    original, native = args.stock.read_bytes(), args.native.read_bytes()
    report = {'stock_sha256': hashlib.sha256(original).hexdigest(),
              'native_sha256': hashlib.sha256(native).hexdigest(),
              'controllers': verify(original, native), 'hardware_verified': False}
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text, end='')
