#!/usr/bin/env python3
"""Execute TYFZA1 Assign-ONU-ID processing before O5 activation."""
from pathlib import Path
import hashlib,json,struct
from arm64_module_oracle import ModuleOracle
root=Path(__file__).resolve().parents[1]
module=root/'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
records=[]
for mode in (4,5):
 calls=[]
 def callback(name,args):
  if name in ('ca_printf','cap_log_msg','aal_ngpon2_ploam_assign_onuid_handler'):return 0
  if name=='aal_xgpon_bwmp_tcont_get':
   oracle.uc.mem_write(args[3],struct.pack('<III',17,1,0));return 0
  if name in ('aal_xgpon_usbg_xgem_cfg_set','aal_xgpon_dsfp_xgem_set'):
   row=list(struct.unpack('<III',oracle.uc.mem_read(args[3],12)))
   calls.append({'function':name,'index':args[1],'row':row});return 0
  raise RuntimeError((name,args))
 oracle=ModuleOracle(module,['ploam_ngpon2_proc_assign_onuid'],callback)
 basic=oracle.symbols['g_xgpn_basic_info'][0]
 oracle.uc.mem_write(basic,struct.pack('<II',0x54455354,0x01020304))
 oracle.uc.mem_write(basic+46,bytes([mode]))
 oracle.uc.mem_write(oracle.symbols['queue_num_per_tcont'][0],struct.pack('<I',8))
 base=0x6000000;oracle.uc.mem_map(base,0x10000)
 oracle.word(oracle.symbols['ca_venus_pon_reg_iobase'][0],base)
 oracle.uc.mem_write(base+0x4d30,struct.pack('<I',2))
 packet=bytearray(48);packet[2]=3;packet[5]=17;packet[6:14]=b'TEST\x01\x02\x03\x04'
 oracle.uc.mem_write(oracle.scratch,bytes(packet))
 ret=oracle.call('ploam_ngpon2_proc_assign_onuid',oracle.scratch)
 assert ret==0 and oracle.unpack(basic+44,'H')[0]==17
 assert [c['index'] for c in calls]==[i for i in range(1,8) for _ in range(2)],calls
 assert all(c['row'][0]==17 for c in calls)
 records.append({'mode':mode,'ACTP_fixture_state':2,'return':ret,'saved_ONU_ID':17,'mapping_calls':calls})
result={'factory_module_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
 'actual_function':'ploam_ngpon2_proc_assign_onuid@0xe6c10',
 'scope':'Original PLOAM serial/ONU-ID logic with8-queue capability and ACTP O4 fixture. T-CONT getter, XGEM setters and NGPON channel callback modeled. Mapping dispatch does not wait for O5; no optical registration simulated.',
 'records':records}
(root/'logs/stock-xgpon-assignment.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
