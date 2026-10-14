#!/usr/bin/env python3
"""Trace ITU reset/mode writes in the board's original initializers."""
import json,struct,sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
import os
os.chdir(root)
sys.path.insert(0,str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE
calls=[];writes=[]
def cb(name,args):
 if name in ('ca_printf','printk','ca_mdelay','ca_udelay'):return 0
 if name=='memset':
  o.uc.mem_write(args[0],bytes([args[1]&255])*args[2]);return args[0]
 if name=='memcpy':
  o.uc.mem_write(args[0],bytes(o.uc.mem_read(args[1],args[2])));return args[0]
 calls.append({'function':name,'args':args});return 0
records=[]
for mode,name in [(3,'aal_gpon_init'),(4,'aal_xgpon_init'),(5,'aal_xgpon_init')]:
 o=ModuleOracle('analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',[name],cb)
 calls.clear();writes.clear()
 for symbol,base in [('ca_venus_pon_reg_iobase',0x6000000),('ca_venus_pon_glb_reg_iobase',0x6100000)]:
  o.uc.mem_map(base,0x10000);o.word(o.symbols[symbol][0],base)
 def write_hook(uc,access,address,size,value,data):
  writes.append({'window':'pon' if address<0x6100000 else 'global','offset':address&0xffff,'value':value})
  if address in [0x6006170,0x600612c,0x6006134] and value&0x80000000:
   uc.mem_write(address,struct.pack('<I',value&0x7fffffff))
 o.uc.hook_add(UC_HOOK_MEM_WRITE,write_hook,begin=0x6000000,end=0x610ffff)
 try:
  result=o.call(name,0,mode,instruction_budget=200000)
 except AssertionError:
  from unicorn.arm64_const import UC_ARM64_REG_PC
  pc=o.uc.reg_read(UC_ARM64_REG_PC)
  print("Stall",[(n,hex(a),size) for n,(a,size) in o.symbols.items() if a<=pc<a+size],"writes",writes[-8:],"calls",calls[-8:]);raise
 records.append({'mode':mode,'return':result,'writes':list(writes),'dependency_calls':list(calls)})
 print(mode,result,'global',[(hex(w['offset']),hex(w['value'])) for w in writes if w['window']=='global'],'calls',[x['function'] for x in calls])
Path('logs/itu47-stock-initialization-contract.json').write_text(json.dumps({'dependencies_stubbed':True,'hardware_indirect_busy_completion_modelled':True,'records':records},indent=2)+'\n')
