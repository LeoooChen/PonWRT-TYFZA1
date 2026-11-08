#!/usr/bin/env python3
"""Execute target GetONUInfo request, report parser and EasyMesh UI mapping."""
import argparse,json,struct
from pathlib import Path
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_easymesh import EasyMeshFields
from unicorn.arm64_const import UC_ARM64_REG_SP,UC_ARM64_REG_PC

class InfoOriginal(SettingsOriginal):
 def __init__(self,binary):
  super().__init__(binary)
  self.oracle.symbols.update(info_query=0x412a30,info_parse=0x40ae00)
  self.reported={};self.updated=None;self.fields=[]
 def handler(self,name,a):
  u=self.oracle.uc
  if name=='json_object_object_get':self.fields.append(self.string(a[1]))
  if name=='__isoc99_sscanf':
   from unicorn.arm64_const import UC_ARM64_REG_X6,UC_ARM64_REG_X7
   assert self.string(a[1]) in ('%02X%02X%02X%02X%02X%02X','%02x%02x%02x%02x%02x%02x'),self.string(a[1])
   raw=bytes.fromhex(self.string(a[0]));assert len(raw)==6
   for ptr,byte in zip(a[2:]+[u.reg_read(UC_ARM64_REG_X6),u.reg_read(UC_ARM64_REG_X7)],raw):u.mem_write(ptr,struct.pack('<I',byte))
   return 6
  if name=='memcmp':
   x,y=bytes(u.mem_read(a[0],a[2])),bytes(u.mem_read(a[1],a[2]));return (x>y)-(x<y)
  if name in ('atoi','atoll'):return int(self.string(a[0]))
  if name=='strncpy':
   text=self.string(a[1]).encode();u.mem_write(a[0],text[:a[2]].ljust(a[2],b'\0'));return a[0]
  if name=='mib_chain_total':assert a[0]==3210;return 1
  if name=='mib_chain_get':
   assert a[0]==3210 and a[1]==0
   row=bytearray(1128);row[1:7]=self.peer;u.mem_write(a[2],bytes(row));return 1
  if name=='mib_chain_update':
   assert a[0]==3210 and a[2]==0;self.updated=bytes(u.mem_read(a[1],1128));return 1
  return super().handler(name,a)
 def query_info(self):
  p=self.object({});assert self.oracle.call('info_query',p)==1;return self.objects[p]
 def parse_info(self,value):
  u=self.oracle.uc;u.mem_write(0x44255c,struct.pack('<I',30))
  self.updated=None;self.fields=[]
  u.mem_write(self.packet+0x9000,bytes(4096))
  self.oracle.call('info_parse',self.object(value),self.packet+0x9000)
  return self.updated

def verify(controller,manager,mib):
 s=InfoOriginal(controller);request=s.query_info()
 assert request=={'RPCMethod':'GetONUInfo','MAC':s.mac.hex().upper()}
 fields=EasyMeshFields(manager,mib);o=fields.oracle;sp=o.scratch+0x100000
 rows=[];parsed_fields=[]
 for enabled in (0,1):
  value={'MAC':s.peer.hex().upper(),'easymeshCapability':str(enabled),
         'supportedSsidNumber':'16','roamingSwitch':'1','deviceStatus':'online'}
  stored=s.parse_info(value);assert stored is not None and stored[1127]==enabled
  parsed_fields=list(dict.fromkeys(s.fields))
  s.updated=None
  state,_=s.reply(dict(value,Result=1),pending=20)
  assert state==(0,2,0) and s.updated is not None and s.updated[1127]==enabled
  s.updated=None
  state,_=s.reply({'Result':1},pending=20)
  assert state==(0,2,0) and s.updated is None
  # Actual get_sfu_info province30 output block reads the same MIB row byte.
  # Preceding row selection is explicit; no unrelated hardware calls run.
  output=fields.object({});o.uc.mem_write(sp,bytes(2048));o.uc.mem_write(sp+1591,bytes([stored[1127]]))
  o.uc.mem_write(sp+1640,struct.pack('<Q',output));o.uc.reg_write(UC_ARM64_REG_SP,sp)
  o.uc.emu_start(0x436b64,0x436bc0,count=1000)
  assert o.uc.reg_read(UC_ARM64_REG_PC)==0x436bc0
  assert fields.objects[output]=={'Enableeasymesh':str(enabled),'Supporteasymesh':'1'}
  rows.append(dict(report=value,manager_output=fields.objects[output]))
 return dict(request=request,cases=rows,parsed_fields=parsed_fields,
  scope='Actual GetONUInfo builder and full report parser with modeled single MIB row; actual bounded manager mapping of easymeshCapability byte1127 to Enableeasymesh. Supports reported-state interpretation, not physical Mesh or cryptographic identity verification.')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for name in ('controller','manager','mib','output'):p.add_argument('--'+name,type=Path,required=True)
 a=p.parse_args();result=verify(a.controller,a.manager,a.mib)
 a.output.write_text(json.dumps(result,indent=2)+'\n');print('Original GetONUInfo request, parser and EasyMesh reported-state mapping passed')
