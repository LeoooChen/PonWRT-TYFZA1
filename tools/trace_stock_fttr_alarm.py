#!/usr/bin/env python3
"""Execute original ONU alarm snapshots/notifications and relay request headers."""
import argparse,json,struct,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_fttr_vlan import Slave
from arm64_elf_oracle import ElfOracle

class AlarmSlave(Slave):
 def __init__(self,binary):
  super().__init__(binary);self.rows=[];self.resets=0;self.timer_error=0
  for name in ('omci_alarm_snapshot_create_all','omci_alarm_snapshot_get_by_seq','omci_alarm_snapshot_delete_all'):
   self.oracle.hooks[self.oracle.symbols[name]]="model_"+name
 def handler(self,name,a):
  u=self.oracle.uc
  name=name.removeprefix('model_')
  if name=='feature_api_is_registered':return 0
  if name=='omci_timer_create':return self.timer_error
  if name in ('omci_timer_delete_by_id','omci_timer_restart','omci_alarm_snapshot_delete_all'):return 0
  if name=='omci_alarm_snapshot_create_all':assert a[0]==0;return len(self.rows)
  if name=='omci_alarm_snapshot_get_by_seq':
   if a[0]>=len(self.rows):return 1
   cls,instance,bitmap=self.rows[a[0]]
   u.mem_write(a[1],struct.pack('<H',cls));u.mem_write(a[2],struct.pack('<H',instance));u.mem_write(a[3],bitmap);return 0
  if name=='MIB_GetTableClassId':return a[0]
  return super().handler(name,a)
 def reply_alarm(self,request,rows):
  self.rows=rows;o,p=self.oracle,self.p
  o.uc.mem_write(p,request.ljust(48,b'\0'));o.uc.mem_write(p+256,bytes(1992))
  assert o.call('OMCI_NormalizeMsg',p,p+256)==0
  self.normalized=None
  o.call('OMCI_OnGetAllAlarms' if request[2]==0x4b else 'OMCI_OnGetAllAlarmsNext',p+256)
  assert self.normalized
  o.uc.mem_write(p+4096,self.normalized);o.call('serialize',p+4096)
  return self.wire
 def notify(self,cls,instance,bitmap,sequence=None):
  o,p=self.oracle,self.p
  if sequence is not None:o.uc.mem_write(0x33014,bytes([sequence]))
  o.uc.mem_write(p,bitmap);self.normalized=None;self.wire=None
  assert o.call('omci_alarm_notify_xmit',cls,instance,p)==1
  if self.wire is None:
   assert self.normalized
   o.uc.mem_write(p+4096,self.normalized);o.call('serialize',p+4096)
  return self.wire

def request(binary,next=False,index=0,tci=0x8123):
 def hook(name,a):
  if name=='memset':o.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
  raise AssertionError((name,a))
 o=ElfOracle(binary,hook);p=o.scratch+0x2000
 o.uc.mem_write(p,bytes(48));assert o.call('plr_gponomci_act2mt_meadapter',14 if next else 13,p+512)==0
 mt=int.from_bytes(o.uc.mem_read(p+512,4),'little')
 o.call('plr_gponomci_me_encap_header',tci,mt,2,0,p)
 o.call('plr_gponomci_me_encap_tailer',p+40)
 body=bytearray(o.uc.mem_read(p,44))
 if next:body[8:10]=struct.pack('>H',index)
 return bytes(body)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for n in ('relay','fpga-relay','slave','output'):p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args();s=AlarmSlave(a.slave);reports=[]
 for binary in (a.relay,a.fpga_relay):
  begin=request(binary);assert begin[2:10]==bytes.fromhex('4b0a000200000000')
  rows=[(263,0x8000,b'\x80'+bytes(27)),(256,0,bytes(27)+b'\1')]
  head=s.reply_alarm(begin,rows);assert head[2:10]==bytes.fromhex('2b0a000200000002')
  frames=[]
  for i,row in enumerate(rows):
   req=request(binary,True,i);got=s.reply_alarm(req,rows)
   assert got[2:8]==bytes.fromhex('2c0a00020000') and got[8:12]==struct.pack('>HH',*row[:2]) and got[12:40]==row[2]
   frames.append(dict(request=req.hex(),reply=got.hex()))
  report=dict(binary=binary.name,start=begin.hex(),reply=head.hex(),next=frames)
  sequence=[]
  s.oracle.call('omci_alarm_reset_sequence_number')
  for i in range(257):
   got=s.notify(263,0x8000,rows[0][2]);seq=1+i%255
   assert got[:8]==bytes.fromhex('0000100a01078000') and got[8:36]==rows[0][2] and got[39]==seq
   sequence.append(seq)
  report['notification_sequence']=sequence;reports.append(report)
 a.output.write_text(json.dumps(dict(reports=reports,sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (a.relay,a.fpga_relay,a.slave)},scope='Actual target ONU handlers, reset/notification generator, serializer and both relay header encoders; alarm storage/timers modeled. No physical ONU alarm assertion.'),indent=2)+'\n')
 print('Original alarms: both relay headers, ONU snapshot rows, 514 notifications and1..255 wrap verified')
