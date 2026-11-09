#!/usr/bin/env python3
"""Execute the target WanIPChange producer, envelope and pending ACK dispatch."""
import argparse, ipaddress, json
from pathlib import Path
from trace_stock_fttr_settings import SettingsOriginal

class WanOriginal(SettingsOriginal):
 def __init__(self,binary):
  super().__init__(binary);self.oracle.symbols['wan']=0x411fcc
  # SettingsOriginal captures send; this test needs the actual envelope builder.
  self.oracle.hooks.pop(0x405320)
 def handler(self,name,a):
  if name=='isIPAddr':
   try:return int(ipaddress.ip_address(self.string(a[0])).version==4)
   except ValueError:return 0
  return super().handler(name,a)
 def notification(self,address):
  pointer=self.object({});text=self.text+0x9000
  self.oracle.uc.mem_write(text,address.encode()+b'\0')
  assert self.oracle.call('wan',pointer,text)==1
  return self.objects[pointer]

def verify(controller):
 s=WanOriginal(controller);cases=[]
 for address in ('192.0.2.1','0.0.0.0','255.255.255.255','2001:db8::1','::','::ffff:192.0.2.1'):
  request=s.notification(address);key='WanIP' if ipaddress.ip_address(address).version==4 else 'WanIPv6'
  assert request=={'RPCMethod':'WanIPChange','MAC':s.mac.hex().upper(),key:address}
  frame=s.encode(request)
  assert frame[:16]==s.peer+s.mac+b'\x88\xaa\x56\0'
  assert json.loads(frame[20:])==request
  cases.append(dict(address=address,request=request,frame=frame.hex()))
 ack=[]
 for value in ({'Result':1},{'Result':0},{'RPCMethod':'WanIPChange','Result':1}):
  state,_=s.reply(value,pending=12);assert state==(0,2,0);ack.append(dict(reply=value,state=state))
 return dict(cases=cases,acks=ack,scope='Actual target producer, envelope and pending method12 ACK dispatcher; source MIB, isIPAddr and peer lookup modeled. No subordinate routing effects or physical IO.')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for name in ('controller','output'):p.add_argument('--'+name,type=Path,required=True)
 a=p.parse_args();a.output.write_text(json.dumps(verify(a.controller),indent=2)+'\n')
 print('Original WanIPChange: six IPv4/IPv6 envelopes and three pending replies passed')
