#!/usr/bin/env python3
"""Execute default TYFZA1 LODS and O6 resynchronization handlers."""
from pathlib import Path
import hashlib,json,struct
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE
root=Path(__file__).resolve().parents[1]
module=root/'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
records=[]
for mode in (4,5):
 calls=[];writes=[]
 def callback(name,args):
  if name=='aal_pon_mac_mode_get':return mode
  if name not in ('ca_printf','cap_log_msg'):raise RuntimeError((name,args))
  calls.append(name);return 0
 o=ModuleOracle(module,['aal_xgpn_act_fsm_lods_common_handler','aal_xgpn_act_fsm_s_o6_e_dsync_change'],callback)
 base=0x6000000;o.uc.mem_map(base,0x10000);o.word(o.symbols['ca_venus_pon_reg_iobase'][0],base)
 o.uc.hook_add(UC_HOOK_MEM_WRITE,lambda uc,a,p,s,v,d:writes.append([p-base,v]),begin=base,end=base+0xffff)
 reset=o.unpack(o.symbols['aal_xgpon_mac_reset_at_lods'][0],'I')[0]
 assert reset==0,reset
 assert o.call('aal_xgpn_act_fsm_lods_common_handler',8,10)==0
 assert o.unpack(o.symbols['g_xgpn_act_state'][0],'I')[0]==10
 assert o.call('aal_xgpn_act_fsm_s_o6_e_dsync_change',10,8)==0
 assert o.unpack(o.symbols['g_xgpn_act_state'][0],'I')[0]==8
 assert not writes,writes
 records.append({'mode':mode,'original_reset_at_lods':reset,'LODS_software_state':10,'resynchronized_software_state':8,'PON_MMIO_writes':list(writes),'modeled_log_calls':list(calls)})
result={'factory_module_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
 'actual_functions':['aal_xgpn_act_fsm_lods_common_handler@0x115e50','aal_xgpn_act_fsm_s_o6_e_dsync_change@0x114950'],
 'scope':'Original compiled-default reset policy and handler instructions. Mode getter/logging modeled; no optical timing or OLT emulation. No hardware-reset/timer callee was accepted by the model.',
 'records':records}
(root/'logs/stock-xgpon-o6.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
