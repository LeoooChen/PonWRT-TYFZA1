#!/usr/bin/env python3
"""Execute linked PE capture/refill, ARM ordering, NI tail and RDU accounting."""
from pathlib import Path
import hashlib,json,struct,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'analysis/python-deps'))
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN,UC_HOOK_MEM_WRITE,UC_HOOK_MEM_READ,UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP,UC_MIPS_REG_RA,UC_MIPS_REG_PC
P=Path(sys.argv[1]);binary=(P/'pe-tx.bin').read_bytes()
symbols={s[2]:int(s[0],16) for l in subprocess.check_output(['llvm-nm-18',str(P/'pe-wd.elf')],text=True).splitlines() if len(s:=l.split())==3}
B,MB,BAR,RX,BD,ST,VP,NI,TABLE=0x04000000,0x04002000,0x12000000,0x0a000000,0x07000000,0x08000000,0x13001000,0x09000000,0x07100000
IDS,IDLE,MAP,STATUS,TAIL=0x1a000,0x1a400,0x17400,0xf000,0x17810
# Return sentinels must stay outside a full16KiB instruction image.
class Machine:
 def __init__(self):
  self.u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
  for a,n in ((0x1fc00000,0x10000),(B,0x20000),(BAR,0x10000),(RX,514*16384),(BD,4096),(ST,0x20000),(VP,4096),(NI,4096),(TABLE,4096),(0x10000000,0x10000)):self.u.mem_map(a,n)
  self.put(0x10002300,(1<<31)|(1<<13));self.put(0x100038c4,0xa0000000)
  self.u.mem_write(0x1fc00000,binary);self.tag=8190;self.slot=254;self.pending_w1c=None;self.isr=0;self.acks=[]
  for i,v in ((2,BAR|0xa0000000),(187,B),(80,TABLE),(81,254),(82,8190),(83,254),(134,BD),(177,258),(182,254)):self.put(MB+i*4,v)
  for i in range(514):self.put(TABLE+i*4,self.payload_dma(i))
  for i in range(256):self.put(B+MAP+i*4,i);self.put(BD+i*8+4,self.payload_dma(i))
  for i in range(258):self.put(B+IDLE+i*4,256+i)
  self.put(BAR+0x1218,254<<16|254)
  def write(uc,access,at,size,value,data):
   if at&0x1fffffff==BAR+0x10b4:self.pending_w1c=value;self.acks.append(value)
  def step(uc,at,size,data):
   if self.pending_w1c is not None:
    self.isr&=~self.pending_w1c;self.put(BAR+0x10b4,self.isr);self.pending_w1c=None
  self.u.hook_add(UC_HOOK_MEM_WRITE,write,begin=BAR+0x10b4,end=BAR+0x10b7);self.u.hook_add(UC_HOOK_CODE,step)
 def payload_dma(self,id):return RX+((id*73)%514)*16384
 def put(self,a,v):self.u.mem_write(a,struct.pack('<I',v))
 def get(self,a):return struct.unpack('<I',self.u.mem_read(a,4))[0]
 def execute(self,name):
  for reg,v in ((UC_MIPS_REG_A0,MB|0xa0000000),(UC_MIPS_REG_SP,(B+0x1fff0)|0xa0000000),(UC_MIPS_REG_RA,0x9fc0fff0)):self.u.reg_write(reg,v)
  self.u.emu_start(symbols[name],0x9fc0fff0,count=100000)
  assert self.u.reg_read(UC_MIPS_REG_PC)==0x9fc0fff0
 def capture(self):
  self.execute('pe_rxq')
  if 'rx_notify_done' in symbols:self.execute('rx_notify_done')
 def process(self):self.execute('pe_rx_work')
 def call(self):self.capture();self.process()
 def arrive(self,frame=b'control-payload',data=False):
  at=self.payload_dma(self.get(B+MAP+self.slot*4));self.tag=self.tag%8191+1
  raw=bytearray(36)+frame
  struct.pack_into('<II',raw,0,len(raw)|0xc000|self.tag<<16,len(frame)|1<<31)
  if data:struct.pack_into('<I',raw,16,4);struct.pack_into('<I',raw,24,1<<28|2<<16)
  self.u.mem_write(at,bytes(raw));self.slot=(self.slot+1)&255
  self.put(BAR+0x1218,self.slot<<16|(self.get(BAR+0x1218)&65535))
  return at,bytes(raw)
 def arm_ack(self):
  c=self.get(MB+95*4);assert c!=self.get(MB+94*4)
  work=self.get(B+0xf400+c*4);item=self.get(B+IDS+work*4)&0x7fffffff
  raw=bytes(self.u.mem_read(self.payload_dma(item),50))
  self.put(B+STATUS+work*4,1<<30);self.put(MB+95*4,(c+1)&255)
  return raw
 def inventory(self):
  mapped=[self.get(B+MAP+i*4) for i in range(256)]
  idle=[self.get(B+IDLE+i*4) for i in range(self.get(MB+177*4))]
  busy=[];at=self.get(MB+176*4)
  while at!=self.get(MB+174*4):busy.append(self.get(B+IDS+at*4)&0x7fffffff);at=(at+1)&255
  held=[self.get(B+TAIL+i*8)-1 for i in range(2) if self.get(B+TAIL+i*8)]
  assert sorted(mapped+idle+busy+held)==list(range(514))
  for i,id in enumerate(mapped):assert self.get(BD+i*8+4)==self.payload_dma(id)
  for id in range(514):assert self.get(TABLE+id*4)==self.payload_dma(id)

m=Machine();retained=[]
for n in range(255):
 at,raw=m.arrive(struct.pack('<I',n)+bytes([n&255])*30);retained.append((at,raw));m.call()
 assert m.get(MB+81*4)==m.slot and m.get(BAR+0x1218)&65535==m.slot
assert m.get(MB+175*4)==1 and m.get(MB+94*4)==1
assert m.get(MB+174*4)==255
for at,raw in retained:assert bytes(m.u.mem_read(at,len(raw)))==raw
# A full software busy ring applies bounded backpressure without losing its frame.
at,raw=m.arrive(struct.pack('<I',255)+bytes([255])*30);retained.append((at,raw));m.call()
assert m.get(MB+81*4)!=(m.get(BAR+0x1218)>>16) and m.get(MB+174*4)==255
m.inventory();seen=[]
while m.get(MB+176*4)!=m.get(MB+174*4) or m.get(MB+81*4)!=m.slot:
 if m.get(MB+94*4)!=m.get(MB+95*4):seen.append(struct.unpack_from('<I',m.arm_ack(),36)[0])
 m.call();m.inventory()
assert seen==list(range(256)) and m.get(MB+177*4)==258
for n in range(513):
 m.arrive(struct.pack('<I',n)+bytes(30));m.call();m.arm_ack();m.call();m.inventory()
assert m.get(MB+177*4)==258
# Hardware W1C clears only observed RDU causes; masked IRQs are irrelevant.
m.isr=(1<<19)|(1<<20)|(1<<7);m.put(BAR+0x10b4,m.isr);m.call();m.call()
assert m.get(MB+178*4)==1 and m.get(MB+179*4)==1 and m.isr==1<<7
m.isr|=1<<19;m.put(BAR+0x10b4,m.isr);m.call()
assert m.get(MB+178*4)==2 and m.get(MB+179*4)==1 and m.acks==[(1<<19)|(1<<20),1<<19]

# Live stall: cursor11, cached producer2, hardware producer10, tag still zero.
# RDU must be acknowledged without accepting or discarding that DMA buffer.
blocked=Machine();blocked.slot=11;blocked.tag=5645
for i,v in ((81,11),(82,5645),(83,11),(182,2)):blocked.put(MB+i*4,v)
blocked.put(BAR+0x1218,10<<16|11)
blocked.isr=(1<<19)|(1<<7);blocked.put(BAR+0x10b4,blocked.isr)
blocked.capture()
assert blocked.get(MB+178*4)==1 and blocked.isr==1<<7
assert blocked.get(MB+81*4)==11 and blocked.get(MB+82*4)==5645
assert blocked.get(MB+174*4)==0 and blocked.get(BAR+0x1218)&65535==11
blocked.arrive();blocked.call();blocked.arm_ack();blocked.call();blocked.inventory()
assert blocked.get(MB+81*4)==12 and blocked.get(MB+82*4)==5646

# A producer snapshot covers the whole batch; a later RDU remains observable.
b=Machine();reads=[]
def pci_read(uc,access,at,size,value,data):
 if at&0x1fffffff in (BAR+0x1218,BAR+0x10b4):reads.append(at&0x1fffffff)
b.u.hook_add(UC_HOOK_MEM_READ,pci_read)
for i in range(8):b.arrive()
for i in range(8):b.call()
assert reads.count(BAR+0x1218)==reads.count(BAR+0x10b4)==1
assert b.get(MB+81*4)==b.slot
b.isr=1<<19;b.put(BAR+0x10b4,b.isr);b.call();b.inventory()
assert b.get(MB+178*4)==1 and reads.count(BAR+0x1218)==2

# A preceding ARM exception cannot be overtaken by a protected data frame.
n=Machine();peer=bytes.fromhex('067afb78b287');ap=bytes.fromhex('02e04c076805');dst=bytes.fromhex('0050c2010200')
n.u.mem_write(MB+64,ap);n.u.mem_write(B+0xe410,peer)
n.put(B+0x11a08,1);n.put(ST+0x10008,1<<31|1<<8);n.put(ST+1024,1<<31|0x123)
for i,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST)):n.put(MB+i*4,v)
def data_frame(seq,pn,tid=0):
 hdr=struct.pack('<HH',0x4188,0)+ap+peer+dst+struct.pack('<H',seq<<4)+bytes([tid,0])
 iv=bytes([pn&255,pn>>8,0,0x20,0,0,0,0]);ip=bytearray(20);ip[0]=0x45;ip[9]=17
 return hdr+iv+bytes.fromhex('aaaa030000000800')+ip+bytes(8)+bytes(12)

def reorder_peer(start=0x123,window=64):
 r=Machine();r.u.mem_write(MB+64,ap);r.u.mem_write(B+0xe410,peer)
 r.put(B+0x11a08,1);r.put(ST+0x10008,1<<31|1<<8)
 r.put(ST+1024,1<<31|start);r.put(ST+1028,1);r.put(ST+1024+28,window)
 for j,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST)):r.put(MB+j*4,v)
 return r

# A received BA batch is emitted in SN/PN order, including the4095->0 wrap.
for start,sequences in ((0x123,[0x124,0x123]),(0x123,[0x125,0x124,0x123]),(4094,[0,4095,4094])):
 r=reorder_peer(start)
 for seq in sequences:
  pn=((seq-start)&4095)+2;frame=bytearray(data_frame(seq,pn))
  frame[46:48]=struct.pack('!H',seq)
  r.arrive(bytes(frame),True);r.capture()
 r.process()
 assert r.get(MB+175*4)==0 and r.get(MB+90*4)==1
 for _ in range(len(sequences)*2):r.process()
 expected=[(start+j)&4095 for j in range(len(sequences))]
 actual=[struct.unpack('!H',r.u.mem_read(r.get(NI+j*8)+18,2))[0] for j in range(len(sequences))]
 assert actual==expected and r.get(MB+90*4)==len(sequences)
 assert r.get(ST+1024)==1<<31|((start+len(sequences))&4095)
 assert r.get(ST+1028)==len(sequences)+1 and r.get(MB+191*4)==len(sequences)-1
 assert r.get(MB+175*4)==len(sequences) and not r.get(MB+94*4)
 r.put(VP+0x1c,len(sequences));r.capture();r.inventory()

# PE1 must not give up on an SN gap while PE0 is still capturing the same
# hardware producer snapshot. The missing frame is already DMA-complete.
r=reorder_peer()
r.arrive(data_frame(0x124,3),True);r.arrive(data_frame(0x123,2),True)
r.capture();r.process()
assert r.get(MB+175*4)==0 and not r.get(MB+94*4) and not r.get(MB+90*4)
assert r.get(ST+1024)==1<<31|0x123 and r.get(ST+1028)==1
r.capture()
for _ in range(4):r.process()
assert r.get(MB+175*4)==2 and not r.get(MB+94*4) and r.get(MB+90*4)==2
assert r.get(ST+1024)==1<<31|0x125 and r.get(ST+1028)==3
r.put(VP+0x1c,2);r.capture();r.inventory()

# Missing data with no pending capture still belongs to ARM's reorder timer.
r=reorder_peer();r.arrive(data_frame(0x124,3),True);r.capture();r.process()
assert r.get(MB+94*4)==1 and r.get(MB+175*4)==1 and not r.get(MB+90*4)
r.inventory()
# Waiting cannot deadlock a full work ring with the missing frame behind it.
r=reorder_peer();r.arrive(data_frame(0x124,3),True);r.capture()
for _ in range(254):
 at,_=r.arrive(bytes(32));r.put(at+4,32|1<<31|1<<24);r.capture()
r.arrive(data_frame(0x123,2),True);r.capture();r.process()
assert r.get(MB+94*4)==1 and r.get(MB+175*4)==1 and not r.get(MB+90*4)
assert r.get(ST+1024)==1<<31|0x123 and r.get(ST+1028)==1
r.inventory()

# Full seven-subframe aggregates keep every UDP sequence across NI63->0.
r=reorder_peer();r.put(MB+90*4,61);r.put(MB+91*4,61);r.put(VP+0x1c,61)
expected_payloads={}
for seq in (0x125,0x124,0x123):
 parts=[]
 for part in range(7):
  identity=(seq-0x123)*7+part
  payload=b'TYFPPP-SIM'+struct.pack('!I',identity)+bytes((i*31+7)&255 for i in range(1450))
  ip=bytearray(20);ip[0]=0x45;ip[9]=17;struct.pack_into('!HH',ip,2,1492,identity)
  udp=struct.pack('!HHHH',55000,55001,1472,0)
  llc=bytes.fromhex('aaaa030000000800')+ip+udp+payload
  sub=dst+peer+struct.pack('!H',len(llc))+llc
  parts.append(sub+(bytes((-len(sub))&3) if part<6 else b''))
  expected_payloads[identity]=dst+peer+bytes.fromhex('0800')+ip+udp+payload
 header=bytearray(data_frame(seq,seq-0x123+2)[:34]);header[24]=0x80
 r.arrive(bytes(header)+b''.join(parts)+bytes(12),True);r.capture()
for _ in range(6):r.process()
assert r.get(MB+90*4)==82 and r.get(MB+175*4)==3 and not r.get(MB+94*4)
for identity in range(21):
 address=r.get(NI+((61+identity)&63)*8)
 assert bytes(r.u.mem_read(address,len(expected_payloads[identity])))==expected_payloads[identity]
r.put(VP+0x1c,82&63);r.capture();r.inventory()

# No BA, an outside-window SN, replay PN, or a BAR retain ARM ownership.
for case in ('no_ba','outside_window','replay','BAR','PS_Poll','other_tid','bad_LLC'):
 r=reorder_peer(window=0 if case=='no_ba' else 64)
 seq=0x163 if case=='outside_window' else 0x124
 first,raw_first=r.arrive(data_frame(seq,3),True);r.capture()
 if case in ('BAR','PS_Poll'):
  r.arrive(bytes([0x84 if case=='BAR' else 0xa4,0])+bytes(30));r.capture()
 if case=='other_tid':
  other=bytearray(data_frame(0x123,2));other[24]=1
  r.arrive(bytes(other),True);r.capture()
 candidate=bytearray(data_frame(0x123,1 if case=='replay' else 2))
 if case=='bad_LLC':candidate[34]=0
 last,raw_last=r.arrive(bytes(candidate),True);r.capture();r.process()
 assert r.get(MB+90*4)==0 and r.get(MB+94*4)==1 and r.get(MB+175*4)==1
 assert r.get(ST+1024)==1<<31|0x123 and r.get(ST+1028)==1
 for at,raw in ((first,raw_first),(last,raw_last)):assert bytes(r.u.mem_read(at,len(raw)))==raw
 r.inventory()

# Original reorder controls are per station/TID. A valid independent TID
# can proceed while the earlier TID waits for its missing sequence.
for fault in (None,'head_protocol','other_protocol','other_replay','other_gap','other_PS'):
 r=reorder_peer();other_state=ST+1056
 r.put(other_state,1<<31|0x345);r.put(other_state+4,6);r.put(other_state+28,64)
 frames=[bytearray(data_frame(0x124,3)),bytearray(data_frame(0x345,7,1)),bytearray(data_frame(0x123,2))]
 for packet,identity in zip(frames,(0x124,0x345,0x123)):struct.pack_into('!H',packet,46,identity)
 if fault=='head_protocol':frames[0][34]=0
 if fault=='other_protocol':frames[1][34]=0
 if fault=='other_replay':r.put(other_state+4,7)
 if fault=='other_gap':struct.pack_into('<H',frames[1],22,0x346<<4)
 if fault=='other_PS':frames[1][1]|=0x10
 captured=[r.arrive(bytes(packet),True) for packet in frames]
 for _ in frames:r.capture()
 r.process()
 if fault is None:
  assert r.get(MB+90*4)==2 and r.get(MB+94*4)==0, (r.get(MB+90*4),r.get(MB+94*4))
  for _ in range(4):r.process()
  assert r.get(MB+90*4)==3 and r.get(MB+175*4)==3 and not r.get(MB+94*4)
  assert r.get(ST+1024)==1<<31|0x125 and r.get(ST+1028)==3
  assert r.get(other_state)==1<<31|0x346 and r.get(other_state+4)==7
  actual=[struct.unpack('!H',r.u.mem_read(r.get(NI+j*8)+18,2))[0] for j in range(3)]
  assert actual==[0x345,0x123,0x124]
  assert r.get(MB+192*4)==1
  r.put(VP+0x1c,3);r.capture()
 else:
  assert not r.get(MB+90*4) and r.get(MB+94*4)==1
  assert r.get(ST+1024)==1<<31|0x123 and r.get(ST+1028)==1
  assert r.get(other_state)==1<<31|0x345 and r.get(other_state+4)==(7 if fault=='other_replay' else 6)
  for at,raw in captured:assert bytes(r.u.mem_read(at,len(raw)))==raw
  assert not r.get(MB+192*4)
 r.inventory()

# A stateless PHY report between the two data frames does not change BA order.
r=reorder_peer();r.arrive(data_frame(0x124,3),True);r.capture()
at,_=r.arrive(bytes(32));r.put(at+4,32|1<<31|1<<24);r.capture()
r.arrive(data_frame(0x123,2),True);r.capture();r.process();r.process();r.process();r.process()
assert r.get(MB+90*4)==2 and r.get(MB+175*4)==3 and r.get(MB+94*4)==1
assert r.get(ST+1024)==1<<31|0x125 and r.get(MB+191*4)==1
r.arm_ack();r.put(VP+0x1c,2);r.capture();r.inventory()

# RTS/CTS/ACK/BA do not change receive PN/SN. Lookahead must apply the
# same classification as the normal worker when they split a BA batch.
for fc in (0xb4,0xc4,0xd4,0x94):
 r=reorder_peer();r.arrive(data_frame(0x124,3),True);r.capture()
 control,raw_control=r.arrive(bytes([fc,0])+bytes(30));r.capture()
 r.arrive(data_frame(0x123,2),True);r.capture()
 for _ in range(5):r.process()
 assert r.get(MB+90*4)==2 and r.get(MB+175*4)==3 and r.get(MB+94*4)==1
 assert r.get(ST+1024)==1<<31|0x125 and r.get(ST+1028)==3
 assert bytes(r.u.mem_read(control,len(raw_control)))==raw_control
 assert r.arm_ack()[36]==fc
 r.put(VP+0x1c,2);r.capture();r.inventory()
# PE0 can replenish RX before PE1 runs; PE1's normal TX executor then
# consumes the published RX context without taking over the physical RXQ.
pipeline=Machine();pipeline.u.mem_write(MB+64,ap);pipeline.u.mem_write(B+0xe410,peer)
pipeline.put(B+0x11a08,1);pipeline.put(ST+0x10008,1<<31|1<<8);pipeline.put(ST+1024,1<<31|0x123)
for j,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST)):pipeline.put(MB+j*4,v)
pipeline.arrive(data_frame(0x123,2),True);pipeline.capture()
assert pipeline.get(MB+81*4)==pipeline.slot and pipeline.get(MB+90*4)==0
assert pipeline.get(ST+1024)==1<<31|0x123
tx=0x04500000;pipeline.u.mem_map(tx,0x20000);pipeline.u.mem_map(0x10020000,4096);txmb=tx+0x2000
for j,v in ((0,0x50455744),(3,1),(140,txmb|0xa0000000),(142,1),(188,MB)):pipeline.put(txmb+j*4,v)
pipeline.u.reg_write(UC_MIPS_REG_A0,txmb|0xa0000000)
pipeline.u.reg_write(UC_MIPS_REG_SP,(B+0x1fff0)|0xa0000000)
pipeline.u.emu_start(0xbfc00400,0xbfc0fff0,count=10000)
assert pipeline.get(MB+90*4)==1 and pipeline.get(ST+1024)==1<<31|0x124
assert pipeline.get(MB+81*4)==pipeline.slot and pipeline.get(MB+175*4)==1
pipeline.inventory()
# An empty capture queue needs no worker, while a later packet still waits
# for the outstanding ARM state handler before its PN/SN can advance.
idle=Machine();idle.put(MB+188*4,MB);idle.put(MB+180*4,1)
idle.put(MB+94*4,23);idle.put(MB+95*4,22)
worker_calls=[]
idle_shared_reads=[]
watch=idle.u.hook_add(UC_HOOK_CODE,lambda uc,at,size,data:worker_calls.append(at),
                      begin=symbols['pe_rx_work'],end=symbols['pe_rx_work'])
reads=idle.u.hook_add(UC_HOOK_MEM_READ,lambda uc,access,at,size,value,data:
 idle_shared_reads.append(at) if MB <= at&0x1fffffff < MB+1024 else None)
idle.execute('pe_rx_backend')
idle.u.hook_del(reads)
empty_worker_calls=len(worker_calls)
assert empty_worker_calls==int('--before-idle-check' in sys.argv)
assert len(idle_shared_reads)==(8 if '--before-idle-check' in sys.argv else 5), idle_shared_reads
idle.u.mem_write(MB+64,ap);idle.u.mem_write(B+0xe410,peer)
idle.put(B+0x11a08,1);idle.put(ST+0x10008,1<<31|1<<8);idle.put(ST+1024,1<<31|0x123)
for j,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST)):idle.put(MB+j*4,v)
idle.arrive(data_frame(0x123,2),True);idle.capture();idle.execute('pe_rx_backend')
assert idle.get(MB+90*4)==0 and idle.get(ST+1024)==1<<31|0x123
idle.put(MB+95*4,23);idle.execute('pe_rx_backend');idle.u.hook_del(watch)
assert idle.get(MB+90*4)==1 and idle.get(ST+1024)==1<<31|0x124
assert idle.get(MB+180*4)==0
idle.inventory()
# In the reverse direction PE0 only publishes completions. PE1 alone
# returns BIDs and changes the NI pending count, then notifies ARM.
rpq=0x07500000;pipeline.u.mem_map(rpq,4096);eq_returns=[]
pipeline.put(MB+187*4,tx)
for j,v in ((0,0x50455744),(3,1),(140,MB|0xa0000000),(142,1)):pipeline.put(MB+j*4,v)
for j,v in ((2,BAR|0xa0000000),(30,1),(68,rpq),(72,rpq+1024),(78,1),(116,0x6380),(117,6)):pipeline.put(txmb+j*4,v)
pipeline.put(tx+0xc400,rpq+2048);pipeline.put(tx+0xc404,1<<15|1<<8)
pipeline.put(tx+0x18000+8*4,1)
pipeline.put(rpq,24|0xc000|1<<16);pipeline.put(rpq+4,7<<24|4);pipeline.put(rpq+20,256<<16|1)
pipeline.put(BAR+0x121c,1<<16)
pipeline.put(0x10006340,0x8000007c);pipeline.put(0x10006380,0x80000000)
def eq_write(uc,access,at,size,value,unused):
 if at&0x1fffffff==0x10006384:eq_returns.append(value)
pipeline.u.hook_add(UC_HOOK_MEM_WRITE,eq_write,begin=0x10006384,end=0x10006387)
def actor(control,stack):
 cpu=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
 regions=list(pipeline.u.mem_regions())
 for begin,end,perms in regions:
  cpu.mem_map(begin,end-begin+1,perms)
  cpu.mem_write(begin,bytes(pipeline.u.mem_read(begin,end-begin+1)))
 cpu.hook_add(UC_HOOK_MEM_WRITE,eq_write,begin=0x10006384,end=0x10006387)
 cpu.reg_write(UC_MIPS_REG_A0,control|0xa0000000)
 cpu.reg_write(UC_MIPS_REG_SP,stack|0xa0000000)
 cpu.emu_start(0xbfc00400,0xbfc0fff0,count=10000)
 for begin,end,perms in regions:pipeline.u.mem_write(begin,bytes(cpu.mem_read(begin,end-begin+1)))
actor(MB,B+0x1fff0)
assert pipeline.get(txmb+45*4)==1 and pipeline.get(txmb+46*4)==0
assert pipeline.get(txmb+30*4)==1 and not eq_returns
assert pipeline.get(txmb+189*4)==1 and pipeline.get(txmb+67*4)==0
actor(txmb,tx+0x1fff0)
assert eq_returns==[rpq+2048|6] and pipeline.get(txmb+30*4)==0
assert pipeline.get(txmb+46*4)==1 and pipeline.get(txmb+190*4)==1
assert pipeline.get(txmb+67*4)==1
n.arrive();n.call();n.arrive(data_frame(0x123,2),True);n.call()
assert n.get(ST+1024)==1<<31|0x123 and n.get(MB+90*4)==0
n.arm_ack();n.call();assert n.get(MB+90*4)==1 and n.get(ST+1024)==1<<31|0x124
# Stateless reports/control frames can remain on ARM while data advances.
for kind in (1,8,10,0xb4,0xc4,0xd4,0x94):
 z=Machine();z.u.mem_write(MB+64,ap);z.u.mem_write(B+0xe410,peer)
 z.put(B+0x11a08,1);z.put(ST+0x10008,1<<31|1<<8);z.put(ST+1024,1<<31|0x123)
 for j,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST)):z.put(MB+j*4,v)
 frame=bytes([kind,0])+bytes(30) if kind>=0x80 else bytes(32)
 at,_=z.arrive(frame)
 if kind<0x80:z.put(at+4,32|1<<31|kind<<24)
 z.call();assert z.get(MB+94*4)==1 and z.get(MB+95*4)==0
 z.arrive(data_frame(0x123,2),True);z.call()
 assert z.get(MB+90*4)==1 and z.get(ST+1024)==1<<31|0x124
 assert z.get(MB+94*4)==1 and z.get(MB+95*4)==0
 z.inventory();z.arm_ack();z.call();z.inventory()
# BAR and PS-Poll retain the barrier and keep later data off ARM until ack.
for fc in (0x84,0xa4):
 z=Machine();z.u.mem_write(MB+64,ap);z.u.mem_write(B+0xe410,peer)
 z.put(B+0x11a08,1);z.put(ST+0x10008,1<<31|1<<8);z.put(ST+1024,1<<31|0x123)
 for j,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST)):z.put(MB+j*4,v)
 z.arrive(bytes([fc,0])+bytes(30));z.call();z.arrive(data_frame(0x123,2),True);z.call()
 assert z.get(MB+90*4)==0 and z.get(MB+94*4)==1 and z.get(MB+175*4)==1
 z.arm_ack();z.call();assert z.get(MB+90*4)==1;z.inventory()

first=n.get(NI);payload=bytes(n.u.mem_read(first,60))
assert payload[:14]==dst+peer+bytes.fromhex('0800')
for seq,pn in ((0x124,3),(0x125,4)):n.arrive(data_frame(seq,pn),True);n.call()
n.put(VP+0x1c,3);n.call();n.inventory()
assert bytes(n.u.mem_read(first,60))==payload
assert sum(bool(n.get(B+TAIL+i*8)) for i in range(2))==2
held=[n.get(B+TAIL+i*8)-1 for i in range(2)]
held_bytes={i:bytes(n.u.mem_read(n.payload_dma(i),100)) for i in held}
n.arrive(data_frame(0x126,5),True);n.call()
for i,v in held_bytes.items():assert bytes(n.u.mem_read(n.payload_dma(i),100))==v
n.put(VP+0x1c,4);n.call();n.inventory()
assert sorted(n.get(B+TAIL+i*8+4) for i in range(2))==[3,4]
# End-sequence bit30 and the31-bit NI epoch wrap use the same ownership rule.
for start in (0x3ffffffe,0x7ffffffe):
 w=Machine();w.u.mem_write(MB+64,ap);w.u.mem_write(B+0xe410,peer)
 w.put(B+0x11a08,1);w.put(ST+0x10008,1<<31|1<<8);w.put(ST+1024,1<<31|0x123)
 for j,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST),(90,start),(91,start)):w.put(MB+j*4,v)
 w.put(VP+0x1c,start&63)
 for seq,pn in ((0x123,2),(0x124,3),(0x125,4)):w.arrive(data_frame(seq,pn),True);w.call()
 w.put(VP+0x1c,(start+3)&63);w.call();w.inventory()
 assert sorted(w.get(B+TAIL+j*8+4) for j in range(2))==sorted(((start+2)&0x7fffffff,(start+3)&0x7fffffff))
# Prefix parsing must preserve both WLAN alignments and the complete MPDU
# on an invalid final subframe or insufficient NI descriptor allowance.
for shift,ht,invalid,available in ((0,0,False,63),(2,0,False,63),(0,4,False,63),(2,4,False,63),(2,4,True,63),(2,0,False,1)):
 w=Machine();w.u.mem_write(MB+64,ap);w.u.mem_write(B+0xe410,peer)
 w.put(B+0x11a08,1);w.put(ST+0x10008,1<<31|1<<8);w.put(ST+1024,1<<31|0x123)
 for j,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST),(90,63-available)):w.put(MB+j*4,v)
 hdr=struct.pack('<HH',0x4188|(0x8000 if ht else 0),0)+ap+peer+dst+struct.pack('<H',0x123<<4)+bytes([0x80,0])+bytes(ht)
 iv=bytes([2,0,0,0x20,0,0,0,0]);ip=bytearray(20);ip[0]=0x45;ip[9]=17
 llc=bytes.fromhex('aaaa030000000800')+ip+bytes(8)+bytes([0x5a])*1464
 sub=dst+peer+struct.pack('>H',len(llc))+llc
 last=bytearray(sub)
 if invalid:last[14]=0
 frame=hdr+iv+sub+bytes((-len(sub))&3)+last+bytes(12)
 at,_=w.arrive(bytes(shift)+frame,True)
 if shift:w.put(at+4,len(frame)|1<<31|1<<14)
 raw=bytes(w.u.mem_read(at,36+shift+len(frame)));w.call();w.inventory()
 if invalid or available<2:
  assert bytes(w.u.mem_read(at,len(raw)))==raw and w.get(MB+90*4)==63-available
  assert w.get(ST+1024)==1<<31|0x123 and w.get(MB+94*4)==1
 else:
  assert w.get(MB+90*4)==2 and w.get(ST+1024)==1<<31|0x124
  expected=dst+peer+bytes.fromhex('0800')+llc[8:]
  for j in range(2):assert bytes(w.u.mem_read(w.get(NI+j*8),len(expected)))==expected
# Idle RXQ must not starve RDU handling when only RPQ receives a packet.
r=Machine();r.u.mem_map(0x10020000,4096)
for i,v in ((0,0x50455744),(3,1),(68,BD),(72,ST),(140,MB|0xa0000000),(142,1)):r.put(MB+i*4,v)
r.put(BD,24|0xc000|1<<16);r.put(BD+4,7<<24|4);r.put(BD+20,1<<16|1)
r.put(BAR+0x121c,1<<16)
r.isr=(1<<19)|(1<<20)|(1<<7);r.put(BAR+0x10b4,r.isr)
r.u.reg_write(UC_MIPS_REG_A0,MB|0xa0000000);r.u.reg_write(UC_MIPS_REG_SP,(B+0x1fff0)|0xa0000000)
r.u.emu_start(0xbfc00400,0xbfc0fff0,count=10000)
assert r.get(MB+70*4)==1 and r.get(MB+81*4)==254
assert r.get(MB+178*4)==1 and r.get(MB+179*4)==1 and r.isr==1<<7
# Factory refill contract: all replacement BDs precede one16-bit notify.
for count,stop in ((1,'empty'),(16,'budget'),(7,'tag'),(7,'work_full'),(0,'empty')):
 q=Machine();events=[]
 if stop=='work_full':
  q.put(MB+174*4,248);q.put(MB+175*4,0);q.put(MB+176*4,0)
 for i in range(count):q.arrive(struct.pack('<I',i)+bytes(30))
 if stop=='tag':q.put(BAR+0x1218,((q.slot+1)&255)<<16|254)
 def observe(uc,access,at,size,value,data):
  at&=0x1fffffff
  if BD<=at<BD+2048 or at==BAR+0x1218:events.append((at,size,value))
 q.u.hook_add(UC_HOOK_MEM_WRITE,observe)
 q.execute('pe_rxq_batch')
 notify=[event for event in events if event[0]==BAR+0x1218]
 assert notify==([(BAR+0x1218,2,(254+count)&255)] if count else [])
 assert len([event for event in events if BD<=event[0]<BD+2048])==count
 if count:assert events[-1]==notify[0]
 assert q.get(MB+83*4)==q.get(MB+81*4)==(254+count)&255
 before=len(events);q.execute('pe_rxq_batch');assert len(events)==before
print(json.dumps({'scope':'Actual linked production PE; modeled PCIe, ARM acknowledgement, NI consumption and W1C. No RF throughput simulation.',
 'batch_BD_then_one16bit_notify':True,'short_unready_full_batch_flush':True,'empty_RX_worker_calls':empty_worker_calls,'empty_RX_shared_reads':len(idle_shared_reads),'queued_RX_waits_for_ARM_ACK':True,
 'interleaved_TID_order_and_protocol_barriers':True,
 'PE_sha256':hashlib.sha256(binary).hexdigest(),'ARM_blocked_capture_and_refill_packets':255,'busy_ring_full_payload_preserved':True,
 'ordered_ARM_delivery':len(seen),'additional_wrap_packets':513,'all_514_buffers_have_one_owner':True,'permuted_noncontiguous_DMA_table_all_buffers':True,
 'PE0_capture_then_PE1_executor_processing':True,'PE0_RPQ_then_PE1_BID_return_and_IRQ_notification':True,'BA_batch_SN_PN_order_and_4095_wrap':True,'BA_waits_for_known_PE0_capture_batch':True,'BA_missing_or_full_capture_ring_returns_to_ARM':True,'BA_window_replay_BAR_TID_and_protocol_barriers':True,'sorted_payloads_keep_one_owner':True,'seven_subframe_BA_order_and_NI_wrap':True,'PN_SN_no_overtake':True,'stateless_reports_and_RTS_CTS_ACK_BA_do_not_block_data':True,'BAR_PS_Poll_wait_without_forcing_later_data_to_ARM':True,'NI_last_two_payloads_retained':True,'NI_end_bit30_and_31bit_wrap':True,'AMSDU_prefix_alignment_and_HT_control':True,'invalid_or_NI_full_MPDU_unchanged':True,'RXQ_RPQ_RDU_counted_with_W1C':True,'producer_and_RDU_read_once_per_batch':True,'RPQ_RDU_restarts_with_idle_RXQ':True,'unready_DMA_tag_services_RDU_without_advancing_RX':True},indent=2))
