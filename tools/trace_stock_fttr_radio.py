#!/usr/bin/env python3
"""Execute target Radio RPC encoders, reply consumer and MIB enum converters."""
import argparse,json,struct
from pathlib import Path
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_wifi_fields import WifiFieldsOriginal
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_PC

class RadioOriginal(SettingsOriginal):
 def __init__(self,binary):
  super().__init__(binary)
  self.oracle.symbols.update(radio_query=0x412080,radio_write=0x412a94)
  self.oracle.hooks[0x40ab40]='radio_lookup'
  self.temp=self.oracle.scratch+0xa0000;self.rows=[]
 def textptr(self,text):
  p=self.temp;self.temp+=(len(text.encode())+16)&~7
  self.oracle.uc.mem_write(p,text.encode()+b'\0');return p
 def handler(self,name,a):
  u=self.oracle.uc
  if name=='json_object_get_object':
   table=self.temp;self.temp+=32;previous=0;first=0
   for key,value in self.objects[a[0]].items():
    node=self.temp;self.temp+=32
    u.mem_write(node,struct.pack('<QQQQ',self.textptr(key),0,self.object(value),0))
    if previous:u.mem_write(previous+24,struct.pack('<Q',node))
    else:first=node
    previous=node
   u.mem_write(table,struct.pack('<QQ',0,first));return table
  if name=='json_object_get_array':return a[0]
  if name=='array_list_get_idx':return self.object(self.objects[a[0]][a[1]])
  if name=='json_object_get_string_len':return len(self.objects[a[0]]) if isinstance(self.objects[a[0]],str) else 0
  if name=='strncpy':u.mem_write(a[0],bytes(u.mem_read(a[1],a[2])));return a[0]
  if name=='memset':u.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
  if name=='radio_lookup':return 0
  if name=='mib_chain_get':assert a[:2]==[20063,0];u.mem_write(a[2],bytes(524));return 1
  if name=='mib_chain_update':assert a[0]==20063;self.rows.append(bytes(u.mem_read(a[1],524)));return 1
  if name=='puts':return 0
  return super().handler(name,a)
 def query(self):
  p=self.object({});assert self.oracle.call('radio_query',p)==1;return self.objects[p]
 def write(self,values):
  p=self.object({});data=self.packet+0xb000
  self.oracle.uc.mem_write(data,b''.join(v.encode().ljust(8,b'\0') for v in values))
  assert self.oracle.call('radio_write',p,data)==1;return self.objects[p]

class RadioWidthOriginal(WifiFieldsOriginal):
 # Execute only the setter/getter width blocks. Their surrounding band choice
 # and the width converter are explicit inputs; no radio/syscall is simulated.
 def __init__(self,manager,mib):
  super().__init__(manager,mib);self.values={};self.writes=[]
 def handler(self,name,a):
  if name=='mib_local_mapping_set':
   assert a[0] in (1259,1319) and a[1] in (0,1)
   value=self.oracle.uc.mem_read(a[2],1)[0]
   self.values[a[0]]=value;self.writes.append((a[0],value));return 1
  if name=='mib_get_s':
   assert a[0] in (1259,1319) and a[2]==1 and a[0] in self.values
   self.oracle.uc.mem_write(a[1],bytes([self.values[a[0]]]));return 1
  return super().handler(name,a)
 def roundtrip(self,band,width,code,previous):
  o=self.oracle;sp=o.scratch+0x1fd000
  self.values={1259:previous&3,1319:previous&1};self.writes=[]
  o.uc.mem_write(sp,bytes(2048));o.uc.reg_write(UC_ARM64_REG_SP,sp)
  o.uc.mem_write(sp+85,bytes([code]));o.uc.mem_write(sp+1348,struct.pack('<I',band-1))
  o.uc.mem_write(sp+1376,struct.pack('<Q',self.text_pointer(width)))
  o.uc.emu_start(0x451c94,0x451cf8,count=1000)
  assert o.uc.reg_read(UC_ARM64_REG_PC)==0x451cf8
  writes=list(self.writes);output=self.object({})
  o.uc.mem_write(sp,bytes(2048));o.uc.reg_write(UC_ARM64_REG_SP,sp)
  o.uc.mem_write(sp+1880,struct.pack('<I',band-1))
  o.uc.mem_write(sp+1840,struct.pack('<Q',output))
  o.uc.emu_start(0x44f624,0x44f828,count=1000)
  assert o.uc.reg_read(UC_ARM64_REG_PC)==0x44f828
  return self.objects[output]['FrequencyWidth'],writes

def verify(controller,manager,mib):
 stock=RadioOriginal(controller);fields=WifiFieldsOriginal(manager,mib);width_io=RadioWidthOriginal(manager,mib)
 query=stock.query();assert query=={'RPCMethod':'GetWifiRadio'}
 names=['RadioType','Enable','TransmitPower','Standard','Channel','FrequencyWidth'];cases=[]
 for band,mode,channel,width in (('2.4G','11bgn','6','40M'),('5G','11ac','149','20M')):
  values=[band,'1','80',mode,channel,width]
  request=stock.write(values);assert request==dict(RPCMethod='SetONURadioInfo',**dict(zip(names,values)))
  cases.append(request)
 rows=[dict(MAC='020000000001',OnuMAC=stock.peer.hex().upper(),RadioType='2.4G',Enable='1',TransmitPower='80',Standard='11bgn',Channel='6',AutoChannelEnable='0',FrequencyWidth='40M'),
       dict(MAC='020000000002',OnuMAC=stock.peer.hex().upper(),RadioType='5G',Enable='1',TransmitPower='100',Standard='11ac',Channel='149',AutoChannelEnable='0',FrequencyWidth='Auto20M40M80M')]
 stock.reply(dict(RPCMethod='GetWifiRadio',RadioInfo=rows),pending=0)
 assert len(stock.rows)==1
 row=stock.rows[0]
 for base,item in ((262,rows[0]),(0,rows[1])):
  for key,offset in (('OnuMAC',0),('MAC',13),('RadioType',26),('Enable',31),('TransmitPower',33),('Standard',37),('Channel',43),('FrequencyWidth',117)):
   assert row[base+offset:].split(b'\0',1)[0].decode()==item[key],(key,base)
 enum=[]
 for band in (1,2):
  for width in ('20M','40M','Auto20M40M','Auto20M40M80M','Auto20M40M80M160M'):
   ptr=fields.mib.scratch+0x80000;fields.mib.uc.mem_write(ptr,width.encode()+b'\0')
   result=fields.mib.call('map_wlan_channelWidth_to_mib',band,ptr)
   assert (result&255)==({'20M':0,'40M':1,'Auto20M40M':1,'Auto20M40M80M':2,'Auto20M40M80M160M':3}[width] if band==2 or width in ('20M','40M','Auto20M40M') else 255)
   enum.append(dict(band=band,width=width,result=result&255))
 roundtrips=[]
 for item in enum:
  if item['result']==255:continue
  for previous in range(4):
   observed,writes=width_io.roundtrip(item['band'],item['width'],item['result'],previous)
   assert observed==item['width'],(item,observed,writes)
   if item['result']==1:assert writes[-1]==(1319,int(item['width']=='Auto20M40M'))
   roundtrips.append(dict(band=item['band'],width=item['width'],previous=previous,reported=observed,writes=writes))
 for index,power in enumerate(('100','80','60','40','20')):
  ptr=fields.mib.scratch+0x80000;fields.mib.uc.mem_write(ptr,power.encode()+b'\0')
  assert fields.mib.call('map_wlan_txpower_to_mib',ptr)==index
 return dict(query=query,writes=cases,report=dict(RPCMethod='GetWifiRadio',RadioInfo=rows),widths=enum,width_roundtrips=roundtrips,
  scope='Actual Radio RPC builders, complete two-row reply consumer and MIB width/power mapping plus actual bounded width setter/getter blocks. Radio metadata and commands modeled; no physical channel/power or subordinate application verification.')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for name in ('controller','manager','mib','output'):p.add_argument('--'+name,type=Path,required=True)
 a=p.parse_args();result=verify(a.controller,a.manager,a.mib)
 a.output.write_text(json.dumps(result,indent=2)+'\n');print('Original Radio RPC production, two-band consumption and width/power mapping passed')
