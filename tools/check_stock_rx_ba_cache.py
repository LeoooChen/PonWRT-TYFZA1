#!/usr/bin/env python3
"""Execute stock BA replace/stop and verify cached reorder ownership is revoked."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from arm64_module_oracle import ModuleOracle
module=ROOT/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest=hashlib.sha256(module.read_bytes()).hexdigest();assert digest=='745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
PHL,COMMON,STA,OLD,NEW=0x6000000,0x6010000,0x6020000,0x6030000,0x6040000
trace=[]
def cb(name,args):
 if name=='printk' or name=='__local_bh_enable_ip':return 0
 if name in ('rtw_hal_stop_ba_session','rtw_hal_start_ba_session'):
  trace.append({'call':name});return 0
 if name=='phl_tid_ampdu_rx_alloc':
  trace.append({'call':name});return NEW
 if name=='phl_tid_ampdu_rx_free':
  trace.append({'call':name,'freed':hex(args[0]),'cached_reorder':hex(o.unpack(COMMON+0x7f08,'Q')[0]),'cached_MACID':o.unpack(COMMON+0x7ef0,'I')[0]})
  assert o.unpack(COMMON+0x7f08,'Q')[0]==0 and o.unpack(COMMON+0x7ef0,'I')[0]==256
  return 0
 raise AssertionError((name,args))
o=ModuleOracle(module,['rtw_phl_start_rx_ba_session','rtw_phl_stop_rx_ba_session'],cb,{'vabits_actual':0x500a000})
o.uc.mem_map(PHL,0x60000);o.word(0x500a000,48);o.word(PHL+0x1228,COMMON)
slot=STA+0x928+3*8;o.word(slot,OLD);o.word(COMMON+0x7f08,OLD);o.uc.mem_write(COMMON+0x7ef0,(7).to_bytes(4,'little'))
assert o.call('rtw_phl_start_rx_ba_session',PHL,STA,1,0,512,0,3,64)==0
assert o.unpack(slot,'Q')[0]==NEW
replaced=list(trace);trace.clear()
o.word(COMMON+0x7f08,NEW);o.uc.mem_write(COMMON+0x7ef0,(7).to_bytes(4,'little'))
o.call('rtw_phl_stop_rx_ba_session',PHL,STA,3)
assert o.unpack(slot,'Q')[0]==0
print(json.dumps({'factory_module_sha256':digest,'actual_functions':['rtw_phl_start_rx_ba_session','rtw_phl_stop_rx_ba_session'],'replace':replaced,'stop':trace,'cache_invalidated_before_old_reorder_release':True,'scope':'Actual stock BA pointer/cache invalidation and release-order instructions; HAL BA commands, allocation/free and bottom-half primitives are explicit callbacks. No RF or firmware state simulation.'},indent=2))
