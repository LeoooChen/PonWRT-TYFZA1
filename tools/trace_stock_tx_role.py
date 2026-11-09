#!/usr/bin/env python3
"""Execute original C/XB management and DATA port/MBID descriptor fields."""
import argparse,json,struct,sys,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
setup=(root/'tools/trace_stock_tx_data.py').read_text().split('programs = []',1)[0]
setup=setup.replace("module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'",'module = supplied_module')
setup=setup.replace("names.append('_phl_fill_tx_meta_data')","names += ['_phl_fill_tx_meta_data','txdes_proc_mgnt_8852c','txdes_proc_mgnt_8192xb']")
n={'__file__':str(root/'tools/trace_stock_tx_data.py'),'supplied_module':a.module};exec(compile(setup,n['__file__'],'exec'),n)
o=n['oracle'];records=[]
for chip in ('8852c','8192xb'):
 for kind in ('data','mgnt'):
  for port in range(5):
   for mbid in range(8):
    m=bytearray(96);struct.pack_into('<IIH',m,0,int(kind=='mgnt'),0,324);m[20]=13;m[33]=0x28 if kind=='mgnt' else 0x20;m[34]=1
    struct.pack_into('<Q',m,16,port|(1<<16));struct.pack_into('<Q',m,40,0x1840|mbid<<44)
    o.uc.mem_write(n['metadata'],bytes(m));o.uc.mem_write(n['output'],bytes(128))
    assert not o.call('txdes_proc_'+kind+'_'+chip,n['adapter'],n['metadata'],n['output'],56)
    words=o.unpack(n['output'],'14I');assert words[8]&127==port<<4|mbid,(chip,kind,port,mbid,[hex(w) for w in words])
    baseline=bytearray(m);baseline[16]=0;struct.pack_into('<Q',baseline,40,0x1840)
    o.uc.mem_write(n['metadata'],bytes(baseline));o.uc.mem_write(n['output'],bytes(128))
    assert not o.call('txdes_proc_'+kind+'_'+chip,n['adapter'],n['metadata'],n['output'],56)
    old=o.unpack(n['output'],'14I');assert all(words[i]==(old[i]|(port<<4|mbid) if i==8 else old[i]) for i in range(14))
    records.append(dict(chip=chip,kind=kind,port=port,mbid=mbid,word8=words[8]))
a.output.write_text(json.dumps(dict(sha256=hashlib.sha256(a.module.read_bytes()).hexdigest(),records=records,scope='Actual original descriptor encoders; role metadata supplied, no physical transmission.'),indent=2)+'\n')
print(f'Original TX role: {len(records)} C/XB DATA/management port and MBID fields passed')
