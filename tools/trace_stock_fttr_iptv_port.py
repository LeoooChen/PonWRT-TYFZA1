#!/usr/bin/env python3
"""Execute target IPTV port builders and the actual reply-to-MIB consumer."""
import argparse,json,struct
from pathlib import Path
from trace_stock_fttr_info import InfoOriginal
from trace_stock_fttr_sync import SyncOriginal
class PortOriginal(InfoOriginal):
 def __init__(self,binary):
  super().__init__(binary);self.oracle.symbols.update(port_get=0x411908,port_set=0x41196c)
  self.read_fields=[]
 def handler(self,name,a):
  if name=='json_object_object_get':self.read_fields.append(self.string(a[1]))
  if name=='mib_chain_update':
   assert a[0]==3210 and a[2]==0;self.updated=bytes(self.oracle.uc.mem_read(a[1],1128));return 1
  return super().handler(name,a)
 def ports(self,write=False,ports='1,3',vlan='100'):
  p=self.object({});a=self.text+0x9000;b=a+128
  self.oracle.uc.mem_write(a,ports.encode()+b'\0');self.oracle.uc.mem_write(b,vlan.encode()+b'\0')
  assert self.oracle.call('port_set' if write else 'port_get',p,a,b)==1
  return self.objects[p]
def verify(binary):
 s=PortOriginal(binary);wire=SyncOriginal(binary);cases=[]
 assert s.ports()==dict(RPCMethod='GetIPTVPortInfo',MAC=s.mac.hex().upper())
 for ports in ('','1','1,3','2,4','1,2,3,4','17,23'):
  for vlan in ('0','1','100','4094'):
   query=s.ports(True,ports,vlan)
   assert query==dict(RPCMethod='SetIPTVPortInfo',MAC=s.mac.hex().upper(),IPTVLanPort=ports,IPTVVlanId=vlan)
   s.oracle.uc.mem_write(0x42ea80,struct.pack('<I',1));s.updated=None;s.read_fields=[]
   state,_=s.reply(dict(Result=1,MAC=s.peer.hex().upper(),IPTVLanPort=ports,IPTVVlanId=vlan),pending=25)
   assert state==(0,2,0) and s.updated is not None
   assert s.updated[723:755].split(b'\0',1)[0].decode()==ports
   assert 'IPTVVlanId' not in s.read_fields
   for result in (0,1):assert s.reply(dict(Result=result),pending=26)==((0,2,0),{})
   cases.append(dict(request=query,reported_ports=ports))
 return dict(query=s.ports(),cases=cases,frame=wire.encode(cases[0]['request'],ethertype=0x88aa).hex(),scope='Actual controller builders/encoder and pending25 port-MIB consumer; storage/JSON and remote replies modeled. VLAN is not consumed by the original reader; physical port mapping is unverified.')
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 result=verify(a.controller);a.output.write_text(json.dumps(result,indent=2)+'\n');print('Original IPTVPortInfo:24 string port/VLAN recipes and reply MIB updates; VLAN not read back PASS')
