#!/usr/bin/env python3
"""Execute the original AP packet-padding initializer and HE PHY IE encoder.

Beamforming/STBC setup are boundaries; their fields do not supply pkt_padding.
No IO or live firmware is involved. Only the padding fields are asserted.
"""
from pathlib import Path
import sys,struct,json
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'tools'))
from arm64_module_oracle import ModuleOracle
m=r/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
def cb(n,a):
 if n in ('_phl_init_proto_stbc_cap','_phl_init_proto_bf_cap','printk'):return 0
 raise AssertionError((n,a))
o=ModuleOracle(m,['_phl_init_protocol_cap','rtw_build_he_phy_caps'],cb)
phl,common,cap,adapter,dvobj,out=[0x6000000+i*0x10000 for i in range(6)]
for p in [phl,common,cap,adapter,dvobj,out]:o.uc.mem_map(p,0x10000)
o.word(phl+4648,common);o.word(adapter+192,dvobj);o.word(dvobj+4552,common)
o.uc.mem_write(common+0x58f0+96,b'\x02\x0e');o.uc.mem_write(adapter+0x9456,b'\x80')
o.uc.mem_write(common+0x5873,b'\x02\x02')
rows=[]
for role in (2,3):
 for band in (0,1):
  o.uc.mem_write(cap,bytes(256));o.uc.mem_write(out,bytes(32))
  o.call('_phl_init_protocol_cap',phl,band,role,cap)
  padding=(o.unpack(cap+116,'H')[0]>>9)&3
  assert padding==2,(role,band,padding)
  assert o.call('rtw_build_he_phy_caps',adapter,cap,0,0,out)==11
  phy=bytes(o.uc.mem_read(out,11));assert phy[9]>>6==2 and not phy[6]&128,phy.hex()
  rows.append(dict(role=role,band=band,padding=padding,PPE=bool(phy[6]&128),PHY=phy.hex()))
print(json.dumps({'cases':rows,'pkt_padding_us':16,'PPE_present':False,'boundaries':['beamforming capability setup','STBC capability setup']},indent=2))
