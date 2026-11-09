#!/usr/bin/env python3
"""Execute original IPTV settings selection, wire producer and reply handling."""
import argparse,json,struct
from pathlib import Path
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_sync import SyncOriginal
class IptvOriginal(SettingsOriginal):
 def __init__(self,binary):
  super().__init__(binary);self.oracle.symbols.update(iptv_get=0x4115dc,iptv_set=0x411640)
  self.rows=[];self.failed=set()
 @staticmethod
 def row(mask=1,vid=100,tagged=True,enabled=True,mode=0,service=4):
  data=bytearray(11652);data[206]=int(enabled);data[22]=mode
  struct.pack_into('<I',data,8502,service);struct.pack_into('<Q',data,228,mask)
  data[222]=int(tagged);struct.pack_into('<H',data,223,vid)
  return bytes(data)
 def handler(self,name,a):
  if name=='mib_chain_total':assert a[0]==3004;return len(self.rows)
  if name=='mib_chain_get':
   assert a[0]==3004
   if a[1] in self.failed:return 0
   self.oracle.uc.mem_write(a[2],self.rows[a[1]]);return 1
  if name=='sprintf' and self.string(a[1])=='LAN%d,':
   text=('LAN%d,'%a[2]).encode();self.oracle.uc.mem_write(a[0],text+b'\0');return len(text)
  if name=='strcat':
   x=self.string(a[0]).encode();y=self.string(a[1]).encode();self.oracle.uc.mem_write(a[0],x+y+b'\0');return a[0]
  return super().handler(name,a)
 def iptv(self,write=False,nested=False):
  p=self.object({});assert self.oracle.call('iptv_set' if write else 'iptv_get',p,int(nested))==1
  return self.objects[p]
def verify(binary):
 s=IptvOriginal(binary);wire=SyncOriginal(binary);cases=[]
 query=s.iptv();assert query==dict(RPCMethod='GetIPTVSetting',MAC=s.mac.hex().upper())
 for mask in range(16):
  for tagged in (False,True):
   for vid in (0,1,100,4094):
    s.rows=[s.row(mask,vid,tagged)];request=s.iptv(True)
    fields=dict(IPTVEnable=1,IPTVVlan=vid if tagged else 0,IPTVLanPort=','.join('LAN%d'%(n+1) for n in range(4) if mask&(1<<n))) if mask else dict(IPTVEnable=0)
    assert request==dict(RPCMethod='SetIPTVSetting',MAC=s.mac.hex().upper(),**fields),(mask,tagged,vid,request)
    assert s.iptv(True,True)==fields
    cases.append(dict(mask=mask,tagged=tagged,vid=vid,request=request))
 # Selection stops at the first enabled bridge IPTV service with a port mask.
 s.rows=[s.row(enabled=False),s.row(mode=1),s.row(service=2),s.row(mask=0),s.row(mask=5,vid=42),s.row(mask=2,vid=99)]
 assert s.iptv(True)['IPTVVlan']==42
 s.failed={4};assert s.iptv(True)['IPTVVlan']==99;s.failed=set()
 s.rows=[];assert s.iptv(True,True)==dict(IPTVEnable=0)
 s.rows=[s.row(mask=16,vid=100)];assert s.iptv(True,True)==dict(IPTVEnable=1,IPTVVlan=100,IPTVLanPort='')
 for result in (0,1):
  assert s.reply(dict(Result=result),pending=8)==((0,2,0),{})
  assert s.reply(dict(Result=result),pending=9)==((0,2,0),{})
 sample=cases[47]['request'];frame=wire.encode(sample,ethertype=0x88aa)
 return dict(query=query,cases=cases,sample_frame=frame.hex(),selection='first enabled mode0/service-bit2/nonzero port-mask row; LAN1..4 from low mask bits',scope='Actual original builders/encoder and pending replies. MIB rows, JSON and remote responses modeled; no physical IPTV or VLAN delivery verified.')
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 result=verify(a.controller);a.output.write_text(json.dumps(result,indent=2)+'\n');print('Original IPTV:128 port/tag/VLAN recipes, disabled omission, first eligible WAN selection and ACK handling PASS')
