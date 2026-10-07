#!/usr/bin/env python3
"""Build the TYFZA1 source/bootm RAM boot FIT using the supplied signing key."""
import argparse, json, lzma, struct, subprocess, hashlib
from pathlib import Path

a=argparse.ArgumentParser(description=__doc__)
a.add_argument('image',type=Path); a.add_argument('dtb',type=Path)
a.add_argument('output',type=Path); a.add_argument('--keys',type=Path,required=True)
a.add_argument('--kernel-version',required=True)
a.add_argument('--cpus',type=int,default=1)
args=a.parse_args()
p=Path(__file__).resolve().parents[1]
args.output.mkdir(parents=True,exist_ok=True)
o=args.output.resolve()
image=args.image.read_bytes(); dtb=args.dtb.read_bytes()
load=0x200000; fdt_load=0x1800000
if image[56:60]!=b'ARM\x64': raise ValueError('Expected ARM64 Image')
image_size=struct.unpack_from('<Q',image,16)[0]
if load+max(len(image),image_size)>fdt_load: raise ValueError('Kernel overlaps DTB load region')
contract=json.loads((p/'evidence/rtl-research/data/receipts/tyfza1-boot-compression.json').read_text())
old={x['name']:x for x in contract['images']}
def compress(data,params):
    return lzma.compress(data,format=lzma.FORMAT_ALONE,filters=[{'id':lzma.FILTER_LZMA1,'dict_size':params['dictionary_bytes'],'lc':params['lc'],'lp':params['lp'],'pb':params['pb']}])
(o/'kernel.lzma').write_bytes(compress(image,old['kernel']['lzma_alone']))
(o/'fdt.lzma').write_bytes(compress(dtb,old['fdt-dts']['lzma_alone']))
cmdline='earlycon console=ttyS0,115200n8 loglevel=7 clk_ignore_unused maxcpus='+str(args.cpus)
(o/'boot.cmd').write_text('setenv bootargs "'+cmdline+'"\nbootm ${unzip_src}\n')
its=(p/'artifacts/prior-native-20261002/signed/ram/image.its').read_text()
its=its.replace('load = <0x0>; entry = <0x0>;',f'load = <{load:#x}>; entry = <{load:#x}>;')
its=its.replace('Linux 6.18.52','Linux '+args.kernel_version)
(o/'image.its').write_text(its)
r=subprocess.run(['mkimage','-f','image.its','-k',str(args.keys.resolve()),'image.itb'],cwd=o,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=True)
(o/'mkimage.log').write_text(r.stdout)
receipt={'kernel_version':args.kernel_version,'cpus':args.cpus,'kernel_load':hex(load),'fdt_load':hex(fdt_load),'image_sha256':hashlib.sha256(image).hexdigest(),'dtb_sha256':hashlib.sha256(dtb).hexdigest(),'fit_sha256':hashlib.sha256((o/'image.itb').read_bytes()).hexdigest(),'fit_bytes':(o/'image.itb').stat().st_size}
(o/'manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
