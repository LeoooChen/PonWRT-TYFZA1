#!/usr/bin/env python3
"""Execute original web-password builders with synthetic values only."""
import argparse,hashlib,json
from pathlib import Path
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_sync import SyncOriginal
class PasswordOriginal(SettingsOriginal):
 def __init__(self,binary):
  super().__init__(binary);self.oracle.symbols.update(password_get=0x41133c,password_set=0x4113a0)
  self.password='synthetic-password'
 def handler(self,name,a):
  if name=='mib_get_s' and a[0]==175:
   assert a[2]==30
   value=self.password.encode();assert len(value)<=29
   self.oracle.uc.mem_write(a[1],value.ljust(30,b'\0'));return 1
  return super().handler(name,a)
 def password_request(self,write=False,nested=False):
  p=self.object({});assert self.oracle.call('password_set' if write else 'password_get',p,int(nested))==1
  return self.objects[p]
def verify(binary):
 s=PasswordOriginal(binary);wire=SyncOriginal(binary);rows=[]
 assert s.password_request()==dict(RPCMethod='GetWebPassword',MAC=s.mac.hex().upper())
 for password in ('','x','synthetic-"\\pass','x'*29,'合成密码'):
  s.password=password;fields=dict(WebAdminPassword=password)
  request=s.password_request(True)
  assert request==dict(RPCMethod='SetWebPassword',MAC=s.mac.hex().upper(),**fields)
  assert s.password_request(True,True)==fields
  frame=wire.encode(request,ethertype=0x88aa)
  assert json.loads(frame[20:])==request
  for result in (0,1):
   assert s.reply(dict(fields,Result=result,MAC=s.peer.hex().upper()),pending=4)==((0,2,0),{})
   assert s.reply(dict(Result=result),pending=5)==((0,2,0),{})
  rows.append({'synthetic_bytes':len(password.encode()),'request_and_nested_match':True})
 return dict(controller_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),cases=rows,query=s.password_request(),scope='Original builders, encoder and pending ACK handler; synthetic password/MIB175 only. Values omitted from artifact. No credential installation or login verification.')
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 result=verify(a.controller);a.output.write_text(json.dumps(result,indent=2)+'\n');print('Original WebPassword: five synthetic byte-length/escaping recipes, nested form and ACK/reject handling PASS')
