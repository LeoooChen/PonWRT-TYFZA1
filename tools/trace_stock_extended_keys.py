#!/usr/bin/env python3
"""Execute target two-half key producer and independent DCTL slot insertion."""
import argparse,json,struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X0
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
events=[];fail=0
def callback(name,args):
 if name in ('rtw_hal_mac_add_key','rtw_hal_mac_delete_key'):
  args=[o.uc.reg_read(UC_ARM64_REG_X0+i) for i in range(8)]
  events.append(dict(operation=name,macid=args[1],cipher=args[2],extended=args[3],keyid=args[5],kind=args[6],key=bytes(o.uc.mem_read(args[7],16)).hex() if name.endswith('add_key') else None))
  return 1 if len(events)==fail else 0
 if name=='mac_role_srch':return role
 if name.startswith('upt_dctl_secinfo_entry_'):
  events.append(dict(slot=args[2],state=bytes(o.uc.mem_read(entry+8,17)).hex()));return 0
 raise AssertionError((name,args))
names=['rtw_hal_set_key']+[n+'_'+c for c in ('8852c','8192xb') for n in ('insert_key_to_dmac_tbl','decide_key_index','check_key_index')]
o=ModuleOracle(a.module,names,callback)
adapter,hw,table,entry,role,sta,data=[0x6000000+i*0x10000 for i in range(7)]
for address in (adapter,hw,table,entry,role,sta,data):o.uc.mem_map(address,0x10000)
o.word(adapter+88,hw);o.word(hw+112,table);o.word(table+7*8,entry);o.word(role+96,3<<48)
o.uc.mem_write(sta+28,b'\7');o.uc.mem_write(data,bytes(range(32)))
rows=[]
for cipher in (7,9):
 for kind in (0,1):
  for keyid in range(4):
   for fail in (0,1,2):
    events=[];ret=o.call('rtw_hal_set_key',adapter,sta,cipher,1,0,keyid,kind,data)
    assert len(events)==2 and [e['extended'] for e in events]==[1,1]
    assert [e['key'] for e in events]==[bytes(range(16)).hex(),bytes(range(16,32)).hex()]
    assert ret==(fail==2) # Target overwrites the first call's failure.
    rows.append(dict(cipher=cipher,kind=kind,keyid=keyid,fail=fail,result=ret,events=events))
   fail=0;events=[];assert not o.call('rtw_hal_set_key',adapter,sta,cipher,1,0,keyid,kind,0)
   assert [e['extended'] for e in events]==[0,1]
for chip in ('8852c','8192xb'):
 for kind,start in ((0,0),(1,2)):
  for cam1,cam2 in ((0,1),(63,7),(127,0)):
   o.uc.mem_write(entry,bytes(32));events=[]
   assert not o.call('insert_key_to_dmac_tbl_'+chip,adapter,7,kind,1,cam1)
   assert not o.call('insert_key_to_dmac_tbl_'+chip,adapter,7,kind,1,cam2)
   assert [e['slot'] for e in events]==[start,start+1]
   assert o.unpack(entry+10,'B')[0]==3<<start
   assert bytes(o.uc.mem_read(entry+11+start,2))==b'\1\1'
   assert bytes(o.uc.mem_read(entry+18+start,2))==bytes([cam1,cam2])
   rows.append(dict(chip=chip,kind=kind,cams=[cam1,cam2],events=events))
for chip in ('8852c','8192xb'):
 for slot in range(7):
  assert o.call('check_key_index_'+chip,3,1,slot)==(0 if 2<=slot<=5 else 1)
  assert o.call('check_key_index_'+chip,2,1,slot)==(0 if 2<=slot<=4 else 1)
a.output.write_text(json.dumps(dict(cases=rows,scope='Actual target HAL half producer and DCTL slot allocator. Independent nonadjacent CAMs and same key ID; H2C/physical AES not executed. First-half failure is overwritten by the target wrapper.'),indent=2)+'\n')
print('Extended key:48 producer/failure cases,16 deletions,12 independent dual-DCTL placements passed')
