#!/usr/bin/env python3
"""Record original ITU management/business PDC selector semantics."""
import json,struct,sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
import os
os.chdir(root)
sys.path.insert(0,str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE,UC_HOOK_MEM_READ
base=0x6000000;rows={};writes=[]
def cb(n,a):
 if n=='memset':o.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
 if n in ['ca_spin_lock_init','ca_spin_lock','ca_spin_unlock','ca_printf','printk']:return 0
 raise RuntimeError((n,a))
o=ModuleOracle('analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',['aal_pdc_init','aal_pdc_map_mem_set','aal_pdc_ctrl_set'],cb)
o.uc.mem_map(base,0x10000);o.word(o.symbols['ca_venus_pon_reg_iobase'][0],base)
o.uc.mem_write(base+0x9014,struct.pack('<I',2))
def wh(uc,access,a,size,v,data):
 writes.append((a-base,v))
 if a==base+0x9020 and v&0x40000000:rows[v&255]=list(struct.unpack('<II',uc.mem_read(base+0x9024,8)))
def rh(uc,access,a,size,v,data):
 if a==base+0x9020:
  v=struct.unpack('<I',uc.mem_read(a,4))[0];uc.mem_write(a,struct.pack('<I',v&0x7fffffff))
o.uc.hook_add(UC_HOOK_MEM_WRITE,wh,begin=base,end=base+0xffff);o.uc.hook_add(UC_HOOK_MEM_READ,rh,begin=base,end=base+0xffff)
assert o.call('aal_pdc_init',0,3,instruction_budget=1000000)==0
assert len(rows)==256
ctrl=struct.unpack('<I',o.uc.mem_read(base+0x9014,4))[0]
print('Factory GPON PDC control',hex(ctrl),'management rows',[(i,[hex(x) for x in rows[i]]) for i in [0,6,7]],'business row8',[hex(x) for x in rows[8]])
Path('logs/itu49-original-pdc-contract.json').write_text(json.dumps({'rows':rows,'control':ctrl,'MMIO_completion_modelled':True,'hardware_test':False},indent=2)+'\n')
