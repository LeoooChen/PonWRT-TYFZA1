#!/usr/bin/env python3
"""Execute original EasyMesh request production and Flag2 completion handling."""
import argparse, json, struct
from pathlib import Path
from trace_stock_fttr_wifi_fields import WifiFieldsOriginal
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_sync import SyncOriginal

class EasyMeshOriginal(SettingsOriginal):
 def handler(self,name,a):
  if name=='mib_get_s' and a[0]==736:
   assert a[2]==4
   self.oracle.uc.mem_write(a[1],struct.pack('<I',4));return 1
  return super().handler(name,a)

class EasyMeshFields(WifiFieldsOriginal):
 def handler(self,name,a):
  if name=='memset':
   self.oracle.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
  return super().handler(name,a)

def verify(manager,mib,controller):
 fields=EasyMeshFields(manager,mib);stock=EasyMeshOriginal(controller);wire=SyncOriginal(controller)
 cases=[]
 for enabled in ('0','1'):
  fields.sent.clear()
  fields.oracle.call('set_fu_easymesh_info',0,fields.text_pointer(json.dumps({
   'Enableeasymesh':enabled,'MAC':fields.peer.hex().upper()})),fields.object({}))
  expected={'act':'config','EnableEasymesh':enabled}
  assert fields.sent==[dict(flag=2,content=expected,mac=fields.peer.hex().upper())]
  stock.request_content=expected
  request=stock.wifi_query()
  assert request==dict(RPCMethod='SetAllSetting',MAC=stock.mac.hex().upper(),
                      DMAC=stock.peer.hex().upper(),Flag=2,content=expected)
  frame=wire.encode(request,ethertype=0x88aa)
  # The target needs a separate STATUS0 report after its transport ACK.
  before=len(stock.flags_cleared)
  assert stock.reply({'Result':1},pending=11)==((0,2,0),{})
  assert len(stock.flags_cleared)==before
  report=dict(RPCMethod='SetAllSetting',Flag=2,STATUS=1)
  stock.reply(report,pending=0);assert len(stock.flags_cleared)==before
  report['STATUS']=0
  stock.reply(report,pending=0);assert stock.flags_cleared[-1]==2
  cases.append(dict(enabled=enabled,request=request,frame=frame.hex(),completion=report))
 return dict(cases=cases,scope='Actual fttrManage EasyMesh producer, sync_controllerd Flag2 builder, L2 encoder and completion handler; source MIB, JSON-C, files and slave reports modeled. No slave application/readback or roaming operation verified.')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for name in ('manager','mib','controller','output'):p.add_argument('--'+name,type=Path,required=True)
 a=p.parse_args();result=verify(a.manager,a.mib,a.controller)
 a.output.write_text(json.dumps(result,indent=2)+'\n')
 print('Original EasyMesh: enabled/disabled Flag2 production, L2 frames, ACK versus STATUS completion passed')
