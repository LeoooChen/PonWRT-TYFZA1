#!/usr/bin/env python3
"""Target optical Test encoder, result parser, conversion and ONU result producer."""
import argparse,hashlib,json,struct
from pathlib import Path
from arm64_elf_oracle import ElfOracle

class Optical:
 def __init__(self,binary):
  self.frames=[];self.raw=[165,13500,16500,5000,6400];self.fail=0
  self.o=ElfOracle(binary,self.hook,('plr_gponomci_me_urgent_prio_tx','plr_gponrm_card_pon_ani_get_by_ani_id','plr_gponrm_pr_anig_test'))
  self.p=self.o.scratch+0x2000
  self.o.symbols['encoder']=self.o.symbols['plr_gponomci_getresp_meadapter']+0xb4
 def hook(self,name,a):
  u=self.o.uc
  if name=='memset':u.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
  if name=='plr_gponomci_me_urgent_prio_tx':self.frames.append(bytes(u.mem_read(a[2],a[3])));return 0x80000000
  if name=='plr_gponrm_card_pon_ani_get_by_ani_id':
   assert a[1]==0;u.mem_write(self.p+1024+16,struct.pack('<H',0x8000));return self.p+1024
  if name=='plr_gponrm_pr_anig_test':
   assert a[1]==0 and struct.unpack('<H',u.mem_read(a[2],2))[0]==0x8000
   u.mem_write(a[2]+2,struct.pack('<5H',*self.raw));return self.fail
  if name=='plr_log':return 0
  raise AssertionError((name,a))
 def request(self,instance=0x8000,tci=0x8123):
  self.frames=[];self.o.uc.mem_write(self.p,struct.pack('<IIHH',7,16,0,instance))
  assert self.o.call('encoder',tci,263,self.p)==0x80000000
  assert len(self.frames)==1;return self.frames[0][:44]
 def parse(self,body):
  self.o.uc.mem_write(self.p,body);self.o.uc.mem_write(self.p+512,bytes(12))
  assert self.o.call('plr_gponomci_me_testresult_optline_parse',263,self.p,self.p+512,12)==0x80000000
  return list(struct.unpack('<5H',self.o.uc.mem_read(self.p+514,10)))
 def convert(self,raw):
  self.raw=raw;self.o.uc.mem_write(self.p+512,bytes(10))
  assert self.o.call('plr_gponrm_card_runtest_ont_optline',self.p,self.p+512)==0
  return list(struct.unpack('<5h',self.o.uc.mem_read(self.p+512,10)))

class SlaveOptical:
 def __init__(self,binary):
  self.values={8:-3.,7:3.,5:33000.,6:5000.,4:25.};self.missing=set();self.packet=None
  self.o=ElfOracle(binary,self.hook);self.o.symbols['handler']=0x3744
  self.p=self.o.scratch+0x2000
 def hook(self,name,a):
  u=self.o.uc
  if name=='MIB_Get':u.mem_write(a[1]+2,bytes(a[2]-2));return 0
  if name=='anig_generic_transceiver_para_updater':
   if a[1] in self.missing:return 1
   u.mem_write(a[0],struct.pack('<d',self.values[a[1]]));return 0
  if name=='GOS_Htons':return int.from_bytes(struct.pack('<H',a[0]&65535),'big')
  if name=='MIB_Set':return 0
  if name=='OMCI_SendMsg':
   assert a[4]==1992
   self.packet=bytes(u.mem_read(a[3],1992));return 0
  if name=='free':return 0
  raise AssertionError((name,a))
 def result(self,request,missing=()):
  self.missing=set(missing);self.packet=None
  # Native OMCI message retains TCI/class/instance, opcode in byte7,
  # content at20. The original handler fills result triplets at20..34.
  packet=bytearray(1992);packet[2:4]=request[:2][::-1];struct.pack_into('<IHH',packet,8,10,263,int.from_bytes(request[6:8],'big'));packet[20]=7
  self.o.uc.mem_write(self.p,bytes(packet));assert self.o.call('handler',self.p)==0
  actual=self.packet;assert actual and actual[7]==0x1b
  return request[:2]+b'\x1b\x0a'+request[4:8]+actual[20:52]+b'\0\0\0\x28'

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for n in ('relay','fpga-relay','slave','output'):p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args();results=[]
 for binary in (a.relay,a.fpga_relay):
  o=Optical(binary);request=o.request();assert request[2:9]==bytes.fromhex('520a0107800007')
  slave=SlaveOptical(a.slave)
  frame=slave.result(request);raw=o.parse(frame);converted=o.convert(raw)
  assert raw==[165,13500,16500,5000,6400] and converted==[330,-300,300,1000,2500],(raw,converted)
  for i in range(5):
   missing=(5,8,7,6,4)[i];body=slave.result(request,[missing]);assert body[8+i*3:11+i*3]==bytes(3)
  cases=0
  for value in (0,1,255,256,1000,13500,16500,32767,32768,65535):
   raw=[value]*5;got=o.convert(raw);signed=value if value<32768 else value-65536
   expected=[signed*2,int(signed/5)-3000,int(signed/5)-3000,value//5,int(signed*100/256)]
   # Factory stores five16-bit values; native presentation avoids voltage overflow.
   assert got==[(x+32768)%65536-32768 for x in expected]
   cases+=1
  results.append(dict(file=binary.name,request=request.hex(),result=frame.hex(),raw=o.parse(frame),converted=converted,conversion_cases=cases,sha256=hashlib.sha256(binary.read_bytes()).hexdigest()))
 a.output.write_text(json.dumps(dict(results=results,scope='Actual target Test encoder/parser/conversion and original ONU result generator. Optical sensor values and MIB storage are explicit boundaries; no physical measurement.'),indent=2)+'\n')
 print('Target ONU optical: both Test encoders, five result IDs/units, missing sensors and20 conversion boundaries passed')
