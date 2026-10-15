#!/usr/bin/env python3
"""Execute the board's original T-CONT dispatch for GPON and XGTC modes."""
from pathlib import Path
import hashlib,json,struct
from arm64_module_oracle import ModuleOracle

root=Path(__file__).resolve().parents[1]
module=root/'analysis/private/live-files/lib/modules/5.10.70/extra/ca-rtk.ko'
records=[]
for mode in (3,4,5):
 calls=[]
 def callback(name,args):
  if name not in ('aal_gpon_tcont_set','aal_xgpon_bwmp_tcont_set'):
   raise RuntimeError((name,args))
  calls.append({'function':name,'arguments':args})
  return 0
 oracle=ModuleOracle(module,['dal_rt_rtl8277c_gpon_tcont_set'],callback,
  external_symbols={'ca_rtk_debug':0x6020000})
 table,output=0x6000000,0x6010000
 oracle.uc.mem_map(table,0x30000)
 # Original gpon_init is at g_gpon_ploam_rx+44; its T-CONT pointer is+72.
 oracle.uc.mem_write(oracle.symbols['gpon_init'][0],struct.pack('<I',1))
 oracle.word(oracle.symbols['g_gpon_ploam_rx'][0]+72,table)
 oracle.uc.mem_write(oracle.symbols['CA_PON_MODE'][0],bytes([mode]))
 for index in range(31):
  oracle.uc.mem_write(table+index*8,struct.pack('<II',index+1,65535))
 ret=oracle.call('dal_rt_rtl8277c_gpon_tcont_set',output,1063)
 expected='aal_gpon_tcont_set' if mode==3 else 'aal_xgpon_bwmp_tcont_set'
 assert ret==0 and [c['function'] for c in calls]==[expected],(mode,ret,calls)
 assert oracle.unpack(output,'I')[0]==0
 records.append({'mode':mode,'return':ret,'calls':calls})
result={'factory_module_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
 'actual_function':'dal_rt_rtl8277c_gpon_tcont_set@0x1db60',
 'scope':'Original current-mode dispatch and table publication; initialized T-CONT state supplied, AAL hardware bodies modeled; no optical execution or native upload scheduling simulation.',
 'records':records}
(root/'logs/stock-pon-family-paths.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
