#!/usr/bin/env python3
"""Execute the target RX shortcut's protected/template consistency branch."""
import argparse,json,struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X1,UC_ARM64_REG_X19,UC_ARM64_REG_X22,UC_ARM64_REG_PC
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def boundary(name,args):raise AssertionError((name,args))
o=ModuleOracle(a.module,['core_rxsc_apply_shortcut'],boundary)
entry=o.functions['core_rxsc_apply_shortcut'];frame=o.scratch+0x1000;attrib=o.scratch+0x2000
assert o.unpack(entry+0x34,'I')[0]==0x794002c0
class Outcome(Exception):pass
result=None
def stop(uc,pc,size,data):
 global result
 if pc in (entry+0x5c,entry+0x80,entry+0xb8):
  result=pc==entry+0x5c;raise Outcome
o.uc.hook_add(UC_HOOK_CODE,stop)
rows=[]
for cipher in (0,6,7,8,9):
 for protected in (False,True):
  for order in (False,True):
   fc=0x0188|(0x4000 if protected else 0)|(0x8000 if order else 0)
   o.uc.mem_write(frame,struct.pack('<H',fc)+bytes(40));o.uc.mem_write(attrib,bytes(128));o.uc.mem_write(attrib+67,bytes([cipher]))
   for reg,value in ((UC_ARM64_REG_X1,attrib),(UC_ARM64_REG_X19,attrib),(UC_ARM64_REG_X22,frame)):o.uc.reg_write(reg,value)
   result=None
   try:o.uc.emu_start(entry+0x34,entry+0x5c,count=100)
   except Outcome:pass
   # Unicorn's until address can finish before the code hook.
   if result is None:
    assert o.uc.reg_read(UC_ARM64_REG_PC)==entry+0x5c
    result=True
   assert result==(protected==bool(cipher))
   assert o.unpack(attrib+64,'BB')==(int(order),int(protected))
   rows.append(dict(cipher=cipher,protected=protected,order=order,accepted=result))
a.output.write_text(json.dumps(dict(cases=rows,scope='Actual target bounded privacy/template branch before power-save processing. Confirms open/open and encrypted/encrypted matching, not full RX acceptance or hardware decryption.'),indent=2)+'\n')
print('Original RX:20 privacy/template/order branches passed, including open/open')
