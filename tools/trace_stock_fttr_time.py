#!/usr/bin/env python3
"""Execute retained Get/SetTimeSetting JSON builders and pending reply handling."""
import argparse,json,struct
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_sync import SyncOriginal
class TimeOriginal(SettingsOriginal):
 def __init__(self,binary):
  super().__init__(binary);self.oracle.symbols.update(time_get=0x411444,time_set=0x4114a8)
  self.values={128:b'\x01',129:struct.pack('<I',24),133:b'192.0.2.123',134:b'2001:db8::123'}
 def handler(self,name,a):
  if name=='json_object_new_int':return self.object(struct.unpack('<i',struct.pack('<I',a[0]&0xffffffff))[0])
  if name=='mib_get_s' and a[0] in self.values:
   value=self.values[a[0]];expected={128:1,129:4,133:46,134:46}[a[0]];assert a[2]==expected
   self.oracle.uc.mem_write(a[1],value.ljust(expected,b'\0'));return 1
  return super().handler(name,a)
 def time(self,write=False,nested=False):
  p=self.object({});assert self.oracle.call('time_set' if write else 'time_get',p,int(nested))==1
  return self.objects[p]
def verify(binary):
 s=TimeOriginal(binary);wire=SyncOriginal(binary);rows=[]
 query=s.time();assert query=={'RPCMethod':'GetTimeSetting','MAC':s.mac.hex().upper()}
 for enabled in (0,1):
  for zone in (0,24,0x7fffffff,0xffffffff):
   s.values[128]=bytes([enabled]);s.values[129]=struct.pack('<I',zone)
   request=s.time(True)
   # json_object_new_int takes signed32; native exposes this raw field exactly.
   expected=dict(TimeEnable=enabled,NTPServer1='192.0.2.123',NTPServer2='2001:db8::123',TimeZone=zone if zone<0x80000000 else zone-0x100000000)
   assert request==dict(RPCMethod='SetTimeSetting',MAC=s.mac.hex().upper(),**expected)
   assert s.time(True,True)==expected
   reply=dict(expected,Result=1,MAC=s.peer.hex().upper())
   assert s.reply(reply,pending=6)==((0,2,0),{})
   assert s.reply({'Result':1},pending=7)==((0,2,0),{})
   assert s.reply({'Result':0},pending=7)==((0,2,0),{})
   rows.append(dict(request=request,reply=reply,frame=wire.encode(request,ethertype=0x88aa).hex()))
 return dict(query=query,rows=rows,scope='Actual target builders/encoder and pending ACK handler; MIB storage, JSON/files and remote reply modeled. Set ACK does not prove NTP service or clock synchronization.')
if __name__=='__main__':
 from pathlib import Path
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 out=verify(a.controller);a.output.write_text(json.dumps(out,indent=2)+'\n');print('Original time settings: query, eight enable/raw-zone builders, nested fields and ACK/reject branches PASS')
