#!/usr/bin/env python3
"""Build a signed, non-initramfs FIT for the existing TYFZA1 UBI slots."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'port/tools'))
from make_boot_fit import compress, make_its

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image', type=Path)
parser.add_argument('dtb', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--keys', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
contract = json.loads((root / 'port/configs/tyfza1-fit-compression.json').read_text())
parameters = {item['name']: item['lzma_alone'] for item in contract['images']
              if item['name'] in ('kernel', 'fdt-dts')}
(out / 'kernel.lzma').write_bytes(compress(args.image.read_bytes(), parameters['kernel']))
(out / 'fdt.lzma').write_bytes(compress(args.dtb.read_bytes(), parameters['fdt-dts']))
(out / 'boot.cmd').write_text('''if test "${root_mtd}" = "31:12"; then
    setenv tyfza1_slot 1
    setenv tyfza1_root ubi_r1
    setenv tyfza1_block /dev/ubiblock0_7
else
    setenv tyfza1_slot 0
    setenv tyfza1_root ubi_r0
    setenv tyfza1_block /dev/ubiblock0_4
fi
setenv bootargs "earlycon console=ttyS0,115200n8 clk_ignore_unused maxcpus=4 ubi.mtd=ubi_device ubi.block=0,${tyfza1_root} root=${tyfza1_block} rootfstype=squashfs ro tyfza1.slot=${tyfza1_slot}"
bootm ${unzip_src}
''')
(out / 'image.its').write_text(make_its(0x200000, 0x200000, 0x2000000, signed=True))
# OpenWrt's staging mkimage has TOOLS_LIBCRYPTO disabled. Use the signer
# already used for successful RAM boots, rather than emitting empty nodes.
subprocess.run(['/usr/bin/mkimage', '-f', 'image.its', '-k', str(args.keys.resolve()), 'image.itb'], cwd=out, check=True, stdout=subprocess.DEVNULL)
for node in ('/images/kernel/signature', '/images/fdt-dts/signature',
             '/images/script/signature', '/configurations/conf-dts/signature'):
    subprocess.run(['fdtget', '-t', 'bx', 'image.itb', node, 'value'],
                   cwd=out, check=True, stdout=subprocess.DEVNULL)
size = (out / 'image.itb').stat().st_size
if size > 0x600000:
    raise ValueError(f'FIT is {size} bytes; stock U-Boot reads at most 0x600000')
print(f'UBI kernel FIT: {size} bytes; existing kernel volumes and root volume IDs retained')
