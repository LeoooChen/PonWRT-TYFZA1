#!/usr/bin/env python3
"""Compare both production board DTs to OEM FDTs and execute FIT strap selection."""
import argparse
import json
import lzma
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'tools'), str(root / 'port/tools')]
from stock_dtb import banked_gpio, cells, fdt_nodes, names, phandles
from make_boot_fit import board_boot_command, make_its
from fit_payload import fit_images

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', required=True, type=Path)
args = parser.parse_args()
dts = root / 'port/overlay/target/linux/rtl9617c/dts'
results = []
with tempfile.TemporaryDirectory(prefix='board-variants-') as directory:
    out = Path(directory)
    for board, suffix in ((1, ''), (0, '-2p5g')):
        original = fdt_nodes((root / 'artifacts/led-xgpon-root-20261017' /
                              f'stock-fdt{board}.dtb').read_bytes())
        preprocessed = out / f'board{board}.dts'
        blob = out / f'board{board}.dtb'
        subprocess.run(['gcc', '-E', '-P', '-x', 'assembler-with-cpp', '-nostdinc',
            '-undef', '-D__DTS__', '-I', str(args.kernel / 'include'),
            str(dts / f'rtl9617c-tyfza1-ubi{suffix}.dts'), '-o', str(preprocessed)], check=True)
        subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(blob),
                        str(preprocessed)], check=True)
        native = fdt_nodes(blob.read_bytes())
        old_handles = phandles(original)
        for node, properties in original.items():
            if not node.startswith('/rtk_gpio_led/') or 'label' not in properties:
                continue
            label = names(properties['label'])[0]
            matching = [p for n, p in native.items() if n.startswith('/leds/') and
                        p.get('label') == properties['label']]
            assert len(matching) == 1, (board, label)
            expected = banked_gpio(old_handles, cells(properties['led-gpio']))
            assert cells(matching[0]['gpios'])[1:] == [expected['native_pin'], expected['flags']], (board, label)
        old_labels = {p['label'] for n, p in original.items()
                      if n.startswith('/rtk_gpio_led/') and 'label' in p}
        assert {p['label'] for n, p in native.items() if n.startswith('/leds/')} == old_labels
        old_button = banked_gpio(old_handles, cells(original['/gpio-keys/btn_led']['gpios']))
        assert cells(native['/led-button/lights-toggle']['gpios'])[1:] == [old_button['native_pin'], old_button['flags']]
        ni = native['/soc/ethernet@4f4300000']
        mdio = next(p for n, p in native.items() if p.get('compatible') == b'realtek,rtl9617c-mdio\0')
        assert 'external-phy-enable-gpios' not in mdio
        assert len(cells(ni['realtek,lan-phys'])) == (4 if board == 1 else 5)
        if board == 0:
            assert cells(mdio['reset-gpios'])[1:] == [128, 1]
            assert cells(mdio['reset-delay-us']) == [30000]
            assert cells(mdio['reset-post-delay-us']) == [300000]
            assert names(ni['phy-names']) == ['sgmii']
            phy = phandles(native)[cells(ni['realtek,lan-phys'])[-1]][1]
            assert cells(phy['reg']) == [7] and 'realtek,hsgmii' in phy
        else:
            assert 'phys' not in ni and 'reset-gpios' not in mdio
        results.append({'strap': board, 'OEM_LED_GPIOs_and_button_match': True,
                        'LAN_ports': len(cells(ni['realtek,lan-phys']))})

    # U-Boot gpio input returns the electrical level as its command status.
    for strap in range(8):
        shim = f'''strap={strap}
unzip_src=0x5000000
gpio() {{ return $(( (strap >> ($2 - 111)) & 1 )); }}
setenv() {{ export "$1=$2"; }}
setexpr() {{ export "$1=$(($2 + $4))"; }}
bootm() {{ printf '%s\\n' "$1"; }}
'''
        result = subprocess.run(['sh'], input=shim + board_boot_command(),
                                text=True, capture_output=True)
        if strap in (0, 1):
            assert result.returncode == 0 and result.stdout.strip() == f'0x5000000#conf-{strap}', result
        else:
            assert result.returncode == 1 and 'Unsupported TYFZA1 board strap' in result.stdout

    for filename in ('kernel.lzma', 'fdt.lzma', 'fdt-2p5g.lzma'):
        content = b'kernel-test' if filename == 'kernel.lzma' else (out / ('board0.dtb' if '2p5g' in filename else 'board1.dtb')).read_bytes()
        (out / filename).write_bytes(lzma.compress(content, format=lzma.FORMAT_ALONE))
    (out / 'boot.cmd').write_text(board_boot_command())
    (out / 'image.its').write_text(make_its(0x200000, 0x200000, 0x2000000, board_variants=True))
    subprocess.run(['mkimage', '-f', 'image.its', 'image.itb'], cwd=out, check=True, capture_output=True)
    for board, image in ((0, 'fdt-2p5g'), (1, 'fdt-dts')):
        actual = subprocess.check_output(['fdtget', '-t', 's', str(out / 'image.itb'),
            f'/configurations/conf-{board}', 'fdt'], text=True).strip()
        assert actual == image
        selected = fit_images(out / 'image.itb', f'conf-{board}')
        assert selected['fdt'] == (out / f'board{board}.dtb').read_bytes()

print(json.dumps({'boards': results, 'all_eight_strap_values_executed': True,
                  'FIT_configurations_match_board_DT': True, 'hardware_test': False}, indent=2))
