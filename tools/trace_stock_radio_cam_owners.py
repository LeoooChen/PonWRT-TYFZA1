#!/usr/bin/env python3
"""Execute target role/CAM allocation, shared BSSID lookup and last-user release."""
import argparse,hashlib,json,struct,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
# Existing adapter/H2C fixture; stop before its unrelated AP port experiments.
setup=(root/'tools/trace_stock_ap_port.py').read_text().split('programs = []',1)[0]
saved=sys.argv;sys.argv=['trace_stock_ap_port.py','--module',str(a.module)]
ns={'__name__':'cam_fixture','__file__':str(root/'tools/trace_stock_ap_port.py')};exec(compile(setup,ns['__file__'],'exec'),ns);sys.argv=saved
o=ns['oracle'];adapter=ns['adapter'];state=ns['state']
head,hal,common,role,sta,info=[0x6080000+i*0x10000 for i in range(6)]
for x in (head,hal,common,role,sta,info):o.uc.mem_map(x,0x10000)
o.word(adapter+608,head);o.word(hal+8,common);o.word(sta+16,role)
records=[]
for chip in ('8852c','8192xb'):
 state.clear();state.update(registers={},operations=[],time_us=0,phase='role')
 hardware=o.symbols['mac'+chip+'_hw_info'][0];assert o.unpack(hardware+40,'H')[0]==128 and o.unpack(hardware+44,'B')[0]==20
 o.word(adapter+88,hardware);o.uc.mem_write(head,bytes(65536))
 pool=head+256;o.word(head,head);o.word(head+8,head);o.word(head+16,pool)
 active={};used=0
 def add(macid,bssid,peer,port=0):
  global used
  node=head+512+used*512;used+=1;o.word(pool,node);o.word(pool+8,node);o.uc.mem_write(pool+24,struct.pack('<I',1));o.word(node,pool);o.word(node+8,pool)
  o.uc.mem_write(role,bytes(4096));o.uc.mem_write(sta,bytes(4096));o.word(sta+16,role)
  o.uc.mem_write(role+8,struct.pack('<I',2));o.uc.mem_write(role+28,bssid);o.uc.mem_write(role+35,bytes([port]))
  o.uc.mem_write(sta+28,struct.pack('<H',macid));o.uc.mem_write(sta+30,peer);o.word(sta+2656,1<<20)
  o.uc.mem_write(info,bytes(512));o.call('_hal_stainfo_to_macrinfo',hal,sta,info,0,0)
  state['operations']=[];status=o.call('mac_add_role',adapter,info)
  assert not status,(chip,macid,status)
  cams=[bytes.fromhex(x['payload']) for x in state['operations'] if x['op']=='H2C' and x['class']==6]
  assert len(cams)==1
  raw=cams[0];addr=raw[4];bss=raw[48]
  assert raw[12]&63==bss
  active[macid]=(addr,bss,bssid);return raw
 # Three BSSIDs and arbitrary MACIDs: each self+peer shares one BSSID entry.
 for macid,bss,peer in ((41,0,0),(7,0,1),(63,1,0),(1,1,1),(100,2,0),(127,2,1)):
  bssid=bytes([2,0,0,0,1,bss]);address=bssid if not peer else bytes([2,0,0,0,2,macid])
  raw=add(macid,bssid,address)
  assert raw[4]==len(active)-1 and raw[48]==bss
  records.append(dict(chip=chip,operation='add',macid=macid,bssid=bssid.hex(),wire=raw.hex()))
 for macid in (41,7,1,63,127,100):
  addr,bss,bssid=active.pop(macid);shared=any(v[1]==bss for v in active.values())
  state['operations']=[];assert not o.call('mac_remove_role',adapter,macid)
  cams=[bytes.fromhex(x['payload']) for x in state['operations'] if x['op']=='H2C' and x['class']==6]
  assert len(cams)==1;raw=cams[0]
  assert raw[4]==addr and not raw[8]&1,(macid,raw.hex(),shared)
  if shared:assert raw[48]==bss and raw[52]&1
  # Target bug: _change_role re-runs BSSID allocation on removal, and can
  # choose a different free entry instead of clearing the owned entry.
  records.append(dict(chip=chip,operation='remove',macid=macid,shared=shared,owned_bssid=bss,changed_index=raw[48]!=bss,bssid_valid=bool(raw[52]&1),wire=raw.hex()))
 raw=add(99,bytes.fromhex('020000000109'),bytes.fromhex('020000000109'));assert raw[4]==0 and raw[48]==0
 records.append(dict(chip=chip,operation='reuse',macid=99,wire=raw.hex()))
assert any(r.get('changed_index') for r in records)
a.output.write_text(json.dumps(dict(sha256=hashlib.sha256(a.module.read_bytes()).hexdigest(),records=records,
 scope='Actual original HAL/MAC role allocation and deletion with explicit H2C/IO boundaries. No physical retirement assertion.'),indent=2)+'\n')
print(f'Original multi-BSSID CAM ownership: {len(records)} create/remove/reuse transitions passed')
