#!/usr/bin/env python3
"""Compare both production board DTs to OEM FDTs and execute OEM FIT selection."""
import argparse
import json
import lzma
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'tools'), str(root / 'port/tools')]
from stock_dtb import cells, fdt_nodes, names, phandles
from make_boot_fit import board_boot_command, make_its
from fit_payload import fit_images

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', required=True, type=Path)
args = parser.parse_args()
dts = root / 'port/overlay/target/linux/rtl9617c/dts'
baseline = json.loads((root / 'scripts/fixtures/tyfza1-board-gpios.json').read_text())['boards']
# Capture the actual PE0/PE1 ATU writes, not a second copy of their address table.
atu = json.loads(subprocess.check_output([sys.executable,
    str(root / 'scripts/test_pe_physical_reset.py'), '--dump-atu'], text=True))
shadow_pages = {page for page in range(0x2000)
                if any(((page ^ base) & mask) == 0 for base, mask in atu)}
results = []
with tempfile.TemporaryDirectory(prefix='board-variants-') as directory:
    out = Path(directory)
    for board, suffix in ((1, ''), (0, '-2p5g')):
        original = baseline[str(board)]
        preprocessed = out / f'board{board}.dts'
        blob = out / f'board{board}.dtb'
        subprocess.run(['gcc', '-E', '-P', '-x', 'assembler-with-cpp', '-nostdinc',
            '-undef', '-D__DTS__', '-I', str(args.kernel / 'include'),
            str(dts / f'rtl9617c-tyfza1-ubi{suffix}.dts'), '-o', str(preprocessed)], check=True)
        subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(blob),
                        str(preprocessed)], check=True)
        native = fdt_nodes(blob.read_bytes())
        reserved = native['/reserved-memory/pe-atu-shadow@fc00000']
        assert 'no-map' in reserved and 'reusable' not in reserved
        reg = cells(reserved['reg'])
        reserved_pages = set()
        for i in range(0, len(reg), 4):
            start = (reg[i] << 32) | reg[i + 1]
            size = (reg[i + 2] << 32) | reg[i + 3]
            assert start % 0x10000 == size % 0x10000 == 0 and size
            reserved_pages.update(range(start >> 16, (start + size) >> 16))
        assert reserved_pages == shadow_pages, 'RAM reservations must match all PE ATU windows exactly'
        for label, expected in original['leds'].items():
            matching = [p for n, p in native.items() if n.startswith('/leds/') and
                        p.get('label') == label.encode() + b'\0']
            assert len(matching) == 1, (board, label)
            assert cells(matching[0]['gpios'])[1:] == expected, (board, label)
        assert {names(p['label'])[0] for n, p in native.items()
                if n.startswith('/leds/')} == set(original['leds'])
        assert cells(native['/led-button/lights-toggle']['gpios'])[1:] == original['lights_toggle']
        ni = native['/soc/ethernet@4f4300000']
        mdio = next(p for n, p in native.items() if p.get('compatible') == b'realtek,rtl9617c-mdio\0')
        lan_phys = cells(ni['realtek,lan-phys'])
        assert [i for i, handle in enumerate(lan_phys) if handle] == ([0, 1, 2, 3] if board == 1 else [0, 1, 2, 4])
        if board == 0:
            assert cells(mdio['external-phy-enable-gpios'])[1:] == [14, 0]
            assert cells(mdio['reset-gpios'])[1:] == [128, 1]
            assert cells(mdio['reset-delay-us']) == [30000]
            assert cells(mdio['reset-post-delay-us']) == [300000]
            assert names(ni['phy-names']) == ['sgmii']
            phy = phandles(native)[cells(ni['realtek,lan-phys'])[-1]][1]
            assert cells(phy['reg']) == [7] and 'realtek,hsgmii' in phy
        else:
            assert 'phys' not in ni and 'reset-gpios' not in mdio
            assert 'external-phy-enable-gpios' not in mdio
        results.append({'strap': board, 'OEM_LED_GPIOs_and_button_match': True,
                        'LAN_ports': sum(bool(handle) for handle in lan_phys),
                        'PE_ATU_reserved_bytes': len(reserved_pages) * 0x10000,
                        'hardware_ports': [i for i, handle in enumerate(lan_phys) if handle]})

    subprocess.run([sys.executable, str(root / 'port/tools/test_boot_fit.py'),
                    'BoardBoot'], check=True)

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

print(json.dumps({'boards': results, 'OEM_board_selection_executed': True,
                  'FIT_configurations_match_board_DT': True, 'hardware_test': False}, indent=2))
