#!/usr/bin/env python3
"""Execute target role identity propagation into JOIN/CAM, CCTL and beacons."""
import argparse,json,struct,sys,hashlib
from pathlib import Path
from arm64_module_oracle import ModuleOracle
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
setup=(root/'scripts/test_radio_he_join.py').read_text().split('source=(root/',1)[0]
setup=setup.replace("'init_hal_spec_8192xb'","'init_hal_spec_8192xb','mac_fw_role_maintain'")
saved=sys.argv;sys.argv=['test',str(a.module)]
n={'__file__':str(root/'scripts/test_radio_he_join.py')};exec(compile(setup,n['__file__'],'exec'),n);sys.argv=saved
jo=n['o'];hal,role,sta,info,out=[n[x] for x in ('hal','role','sta','info','out')]
records=[]
for port in range(5):
 for mbid in range(8 if not port else 1):
  for kind in (0,1,2):
   macid=(port*17+mbid*3+kind)%128;client=kind==2
   jo.uc.mem_write(role+8,struct.pack('<I',1 if client else 3 if mbid else 2));jo.uc.mem_write(role+35,bytes([port]));jo.uc.mem_write(role+1264,bytes([mbid]))
   jo.uc.mem_write(sta+28,struct.pack('<H',macid));jo.uc.mem_write(sta+30,bytes.fromhex('d05bcbd3ebb2') if kind==0 else bytes.fromhex('3a2ea7759af2'))
   jo.uc.mem_write(sta+2332,bytes(4));jo.uc.mem_write(info,bytes(512));jo.uc.mem_write(out,bytes(512))
   jo.call('_hal_stainfo_to_macrinfo',hal,sta,info,5 if client else 2 if kind else 0,kind!=0)
   jo.call('mac_fw_role_maintain',hal,info);maintain=jo.unpack(out,'I')[0]
   jo.call('mac_h2c_join_info',hal,info);join=jo.unpack(out,'I')[0]
   jo.call('role_info_init',hal,info);jo.call('fill_addr_cam_info',hal,info,out)
   records.append(dict(port=port,mbid=mbid,kind=kind,macid=macid,maintain=maintain,join=join,cam_port=jo.unpack(out+32,'I')[0]))
# Run actual C/XB selected CCTL encoders via the existing HAL fixture.
setup=(root/'tools/trace_stock_station_tables.py').read_text().split('records=[]',1)[0]
saved=sys.argv;sys.argv=['test','--module',str(a.module)]
n={'__file__':str(root/'tools/trace_stock_station_tables.py')};exec(compile(setup,n['__file__'],'exec'),n);sys.argv=saved
o=n['oracle'];updates=[]
for chip in ('8852c','8192xb'):
 o.word(n['adapter']+88,o.symbols['mac'+chip+'_hw_info'][0]);ops=o.symbols['mac'+chip+'_ops'][0];o.word(n['ops']+280,o.unpack(ops+280,'Q')[0])
 for row in records:
  o.uc.mem_write(n['role']+8,struct.pack('<I',1 if row['kind']==2 else 3 if row['mbid'] else 2))
  o.uc.mem_write(n['role']+35,bytes([row['port']]));o.uc.mem_write(n['role']+1264,bytes([row['mbid']]))
  o.uc.mem_write(n['sta']+28,struct.pack('<H',row['macid']));o.uc.mem_write(n['sta']+40,struct.pack('<I',chip=='8852c'))
  n['operations'].clear();assert not o.call('rtw_hal_update_cctrl_tbl',n['hal'],n['sta'])
  wires=[x['payload'] for x in n['operations'] if 'payload' in x];assert len(wires)==1
  updates.append(dict(chip=chip,port=row['port'],mbid=row['mbid'],kind=row['kind'],macid=row['macid'],wire=wires[0]))
# Full HAL role -> beacon entry -> MAC encoder, with only allocation/send modeled.
base,hal,adapter,ops,platform,sta,entry,beacon,packet,payload=[0x6000000+i*0x10000 for i in range(10)]
state={'size':0}
def callback(name,v):
 if name=='rtw_phl_get_stainfo_self':return sta
 if name=='h2cb_alloc':state['size']=0;return packet
 if name=='h2cb_put':p=payload+state['size'];state['size']+=v[1];return p
 if name=='copy':bo.uc.mem_write(v[1],bytes(bo.uc.mem_read(v[2],v[3])));return 0
 if name in ('h2c_pkt_set_hdr','h2c_pkt_build_txd','h2c_end_flow','send'):return 0
 raise AssertionError((name,v))
bo=ModuleOracle(a.module,['hal_update_bcn_entry','hal_mac_ax_send_beacon','mac_send_bcn_h2c'],callback)
for x in (base,hal,adapter,ops,platform,sta,entry,beacon,packet,payload):bo.uc.mem_map(x,0x10000)
bo.word(hal+328,adapter);bo.word(adapter,ops);bo.word(adapter+24,platform);bo.word(ops+896,bo.functions['mac_send_bcn_h2c'])
bo.word(platform+88,bo.callback('copy'));bo.word(platform+48,bo.callback('send'));beacons=[]
for chip in ('8852c','8192xb'):
 bo.word(adapter+88,bo.symbols['mac'+chip+'_hw_info'][0])
 for row in records:
  if row['kind']!=0:continue
  bo.uc.mem_write(base+34,bytes([0,row['port']]))
  bo.uc.mem_write(base+1264,bytes([row['mbid']]));bo.uc.mem_write(sta+28,struct.pack('<H',row['macid']))
  frame=bytes(range(64));bo.uc.mem_write(beacon,bytes(1040));bo.uc.mem_write(beacon+16,frame)
  bo.uc.mem_write(beacon+1016,struct.pack('<II',len(frame),4));bo.uc.mem_write(beacon+1028,b'\x0c');bo.uc.mem_write(beacon+1036,struct.pack('<I',3))
  assert not bo.call('hal_update_bcn_entry',base,hal,entry,beacon)
  assert bytes(bo.uc.mem_read(entry+24,4))==bytes([0,row['port'],row['mbid'],row['macid']])
  assert not bo.call('hal_mac_ax_send_beacon',hal,entry)
  wire=bytes(bo.uc.mem_read(payload,state['size']));assert wire[16:]==frame
  beacons.append(dict(chip=chip,port=row['port'],mbid=row['mbid'],macid=row['macid'],wire=wire[:16].hex()))
a.output.write_text(json.dumps(dict(sha256=hashlib.sha256(a.module.read_bytes()).hexdigest(),roles=records,cctl=updates,beacons=beacons,
 scope='Actual target HAL/MAC encoding; specified role fields, no role allocator or physical multi-interface operation.'),indent=2)+'\n')
print(f'Original role identity: {len(records)} JOIN/CAM, {len(updates)} CCTL and {len(beacons)} complete beacon chains')
