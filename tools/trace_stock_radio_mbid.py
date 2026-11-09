#!/usr/bin/env python3
"""Execute target per-MBID enable/disable, DTIM and shared scheduling writes."""
import argparse,hashlib,json,struct,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
setup=(root/'tools/trace_stock_ap_port.py').read_text().split('programs = []',1)[0]
n={'__file__':str(root/'tools/trace_stock_ap_port.py')};saved=sys.argv;sys.argv=['test','--module',str(a.module)];exec(compile(setup,n['__file__'],'exec'),n);sys.argv=saved
o=n['oracle'];s=n['state'];adapter=n['adapter'];params=n['params'];pinfo=n['pinfo'];records=[]
def seed(address,value):
 for i in range(4):s['registers'][address+i]=value>>(i*8)&255
for chip in ('8852c','8192xb'):
 o.word(adapter+88,o.symbols['mac'+chip+'_hw_info'][0])
 for cfg in (1,12,13,19):
  for mbid in (range(1,8) if cfg==19 else range(8)):
   for value in ((0,1) if cfg in (1,19) else (1,3,255)):
    s.clear();s.update(registers={},operations=[],time_us=0,phase='cfg')
    # Identify the actual table addresses via target relocation data.
    mbreg=o.unpack(o.symbols['mbid_ctrl_regl'][0],'I')[0]
    seed(mbreg,0xa5077f01 if cfg==19 and value else 0xa5077fff);seed(0xc400,0x13c04);seed(0xc414,0xc0064)
    o.uc.mem_write(pinfo,bytes([2,2,1])+bytes(61))
    o.uc.mem_write(params,struct.pack('<IIBB',mbid,value,0,0))
    ret=o.call('mac_port_cfg',adapter,cfg,params)
    records.append(dict(chip=chip,cfg=cfg,mbid=mbid,value=value,result=ret,mbreg=mbreg,operations=list(s['operations'])))
 for initial in (0,0xa5077fff):
  s.clear();s.update(registers={},operations=[],time_us=0,phase='init');seed(mbreg,initial)
  o.uc.mem_write(pinfo,bytes(64));o.uc.mem_write(params,struct.pack('<IIIBBBxH2xI',0,0,3,1,7,0,100,50))
  result=o.call('mac_port_init',adapter,params)
  records.append(dict(chip=chip,cfg='init',initial=initial,result=result,operations=list(s['operations'])))
 # Direct port0_mbid_set is part of the factory startup path.
 s.clear();s.update(registers={},operations=[],time_us=0,phase='count');seed(mbreg,0xa5077fff)
 assert not o.call('port0_mbid_set',adapter,0,7,7)
 records.append(dict(chip=chip,cfg='count',operations=list(s['operations'])))
a.output.write_text(json.dumps(dict(sha256=hashlib.sha256(a.module.read_bytes()).hexdigest(),records=records,scope='Actual target MAC register/H2C operations; modeled register state and command transport, no hardware retirement.'),indent=2)+'\n')
print(f'Original MBID: {len(records)} configuration operations captured')
