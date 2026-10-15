#!/usr/bin/env python3
"""Execute TYFZA1's default T-CONT/PUC drain orchestration and masked controls."""
from pathlib import Path
import hashlib,json,struct
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
root=Path(__file__).resolve().parents[1]
module=root/'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
records=[]
for mode in (4,5):
 events=[];flushes=[];writes=[]
 def callback(name,args):
  if name=='aal_pon_mac_mode_get':return mode
  if name in ('ca_printf','ca_spin_lock','ca_spin_unlock'):return 0
  if name in ('ca_udelay','aal_xgpon_backup_puc','aal_xgpon_reset_puc','aal_xgpon_restore_puc'):
   events.append({'function':name,'arguments':args});return 0
  raise RuntimeError((name,args))
 names=['aal_xgpon_flush_tcont_puc','aal_l3_tm_es_ni_port_ena_set',
  '__xgpon_cfg_record_save.constprop.0','__xgpon_disable.constprop.0',
  '__xgpon_cfg_record_recover.constprop.0','aal_xgpon_tcont_flush',
  'aal_xgpon_xgpn_glb_ctrl_set','aal_xgpon_xgpn_glb_ctrl_get']
 oracle=ModuleOracle(module,names,callback)
 pon,glb,ni=0x6000000,0x6100000,0x6200000
 for symbol,base in [('ca_venus_pon_reg_iobase',pon),('ca_venus_pon_glb_reg_iobase',glb),('g_ne_reg_iobase',ni)]:
  oracle.uc.mem_map(base,0x10000);oracle.word(oracle.symbols[symbol][0],base)
 for at,value in ((pon+0x4004,0x41 if mode==4 else 0x82),(pon+0x4600,0x7804),
                  (pon+0x4d0c,0x12345),(pon+0x4d10,0x6789a),(glb+0x22c,0x13),(ni+0x610c,0xa5c3ffff)):
  oracle.uc.mem_write(at,struct.pack('<I',value))
 def read(uc,access,at,size,value,data):
  if at==pon+0x4004:
   word=struct.unpack('<I',uc.mem_read(at,4))[0]
   if word&0x80000000:uc.mem_write(at,struct.pack('<I',word&0x7fffffff))
 def write(uc,access,at,size,value,data):
  writes.append([hex(at),hex(value)])
  if at==pon+0x4004 and value&0x80000000:flushes.append((value>>24)&31)
 oracle.uc.hook_add(UC_HOOK_MEM_READ,read,begin=pon,end=pon+0xffff)
 oracle.uc.hook_add(UC_HOOK_MEM_WRITE,write,begin=pon,end=ni+0xffff)
 ret=oracle.call('aal_xgpon_flush_tcont_puc',0,instruction_budget=1000000)
 assert ret==0 and flushes==list(range(32)),(ret,flushes)
 assert [e['function'] for e in events if e['function']!='ca_udelay']==[
  'aal_xgpon_backup_puc','aal_xgpon_reset_puc','aal_xgpon_restore_puc']
 assert oracle.unpack(pon+0x4004,'I')[0]==(0x41 if mode==4 else 0x82)
 assert oracle.unpack(glb+0x22c,'I')[0]==0x13
 assert oracle.unpack(ni+0x610c,'I')[0]==0xa5c3ffff
 records.append({'mode':mode,'return':ret,'flushed_TCONT':flushes,'modeled_boundaries':events,'actual_MMIO_writes':writes})
result={'factory_module_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
 'actual_functions':names,'scope':'Original admission gating, MAC/SDS save-disable-restore and all32 T-CONT flush requests. PUC backup/reset/restore bodies and hardware flush completion modeled; no optical or FIFO timing proof.','records':records}
(root/'logs/stock-xgtc-queue-recovery.json').write_text(json.dumps(result,indent=2)+'\n')
print('Original XG-PON/XGS-PON:NI8..23 gated, MAC/SDS saved,32 T-CONT flushes, PUC backup/reset/restore and MAC/SDS/NI restored')
