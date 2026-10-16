#!/usr/bin/env python3
"""Execute this board's OM parsing and peer-update decisions with explicit IO."""
from pathlib import Path
import hashlib,json,struct,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from arm64_module_oracle import ModuleOracle
module=ROOT/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest=hashlib.sha256(module.read_bytes()).hexdigest()
assert digest=='745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
ADAPTER,RECV,FRAME,STA,PHLSTA,ROLE,COMMON,DVOBJ,PHL=[0x6000000+i*0x10000 for i in range(9)]
trace=[]
def cb(name,args):
 if name=='_rtw_memcpy':o.uc.mem_write(args[0],bytes(o.uc.mem_read(args[1],args[2])));return args[0]
 if name=='rtw_get_stainfo':return STA
 if name=='printk':return 0
 if name in ('rtw_phl_cmd_set_macid_pause','rtw_phl_cmd_set_macid_pkt_drop','rtw_phl_cmd_change_stainfo','rtw_phl_write8'):
  trace.append({'call':name,'operation':args[2]});return 0
 raise AssertionError((name,args))
o=ModuleOracle(module,['process_a_control_subfield','rtw_build_he_mac_caps'],cb)
o.uc.mem_map(ADAPTER,0xa0000);o.word(ADAPTER+0xc0,DVOBJ);o.word(DVOBJ+0x11c0,PHL)
o.word(ADAPTER+0x20,ROLE);o.word(ROLE,COMMON);o.word(STA,PHLSTA);o.word(RECV+0x128,FRAME)
o.uc.mem_write(COMMON+0x5951,b'\x0f');o.uc.mem_write(COMMON+0x5873,b'\x02\x02')
rows=[]
for name,rss,bw,tss in [('unchanged',2,3,2),('to_80_1SS',1,2,1),('over_local_NSS',3,2,1)]:
 trace.clear();o.uc.mem_write(PHLSTA+0xa60,struct.pack('<Q',2<<17|2<<20));o.uc.mem_write(PHLSTA+0x2c,struct.pack('<I',3))
 raw=3|1<<2|(rss-1)<<6|bw<<9|(tss-1)<<12
 o.uc.mem_write(FRAME,struct.pack('<H',0xc188)+bytes(24)+struct.pack('<I',raw))
 o.call('process_a_control_subfield',ADAPTER,RECV)
 cap=o.unpack(PHLSTA+0xa60,'Q')[0]
 result={'case':name,'A_control':hex(raw),'result_rx_NSS':cap>>17&7,'result_tx_NSS':cap>>20&7,'bandwidth_code':o.unpack(PHLSTA+0x2c,'I')[0],'calls':list(trace)}
 if name=='unchanged':assert not trace and result['bandwidth_code']==3
 if name=='to_80_1SS':
  assert (result['result_rx_NSS'],result['result_tx_NSS'],result['bandwidth_code'])==(1,1,2)
  assert [v['call'] for v in trace]==['rtw_phl_cmd_set_macid_pause','rtw_phl_cmd_set_macid_pkt_drop','rtw_phl_cmd_set_macid_pkt_drop','rtw_phl_cmd_change_stainfo','rtw_phl_cmd_set_macid_pause']
 if name=='over_local_NSS':assert not trace and (result['result_rx_NSS'],result['result_tx_NSS'],result['bandwidth_code'])==(2,2,3)
 rows.append(result)
# OMI support is conditional on HTC_HE and asoc_cap second-word bit2.
capability=[]
for supported in (0,1):
 o.uc.mem_write(RECV,bytes(32));o.uc.mem_write(RECV,struct.pack('<2Q',1<<51,supported<<2));o.uc.mem_write(FRAME,bytes(6))
 assert o.call('rtw_build_he_mac_caps',RECV,FRAME)==6
 data=bytes(o.uc.mem_read(FRAME,6));assert bool(data[3]&2)==bool(supported)
 capability.append({'input_OM_support':bool(supported),'HE_MAC_caps':data.hex(),'OMI_bit':bool(data[3]&2)})
print(json.dumps({'factory_module_sha256':digest,'actual_functions':['process_a_control_subfield@0xb768c','rtw_build_he_mac_caps'],'rows':rows,'capability':capability,'scope':'Original parser, state writes, validation and command-selection branches executed. Station lookup, memory copy and PHL command/MMIO callees supplied; no actual firmware commands or RF. REA call evidence ties the parser to stock RX shortcut. Current native AP does not advertise OMI support; this is not proof a legal OMI exchange or a live loss cause occurred.'},indent=2))
