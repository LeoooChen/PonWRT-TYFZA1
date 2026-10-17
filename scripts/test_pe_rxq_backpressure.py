#!/usr/bin/env python3
"""Execute linked PE capture/refill, ARM ordering, NI tail and RDU accounting."""
from pathlib import Path
import hashlib,json,struct,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'analysis/python-deps'))
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN,UC_HOOK_MEM_WRITE,UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP,UC_MIPS_REG_RA,UC_MIPS_REG_PC
P=Path(sys.argv[1]);binary=(P/'pe-tx.bin').read_bytes()
symbols={s[2]:int(s[0],16) for l in subprocess.check_output(['llvm-nm-18',str(P/'pe-wd.elf')],text=True).splitlines() if len(s:=l.split())==3}
B,MB,BAR,RX,BD,ST,VP,NI,TABLE=0x04000000,0x04002000,0x12000000,0x0a000000,0x07000000,0x08000000,0x13001000,0x09000000,0x07100000
IDS,IDLE,MAP,STATUS,TAIL=0x1a000,0x1a400,0x17400,0xf000,0x17810
class Machine:
 def __init__(self):
  self.u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
  for a,n in ((0x1fc00000,0x10000),(B,0x20000),(BAR,0x10000),(RX,514*16384),(BD,4096),(ST,0x20000),(VP,4096),(NI,4096),(TABLE,4096),(0x10000000,0x10000)):self.u.mem_map(a,n)
  self.put(0x10002300,(1<<31)|(1<<13));self.put(0x100038c4,0xa0000000)
  self.u.mem_write(0x1fc00000,binary);self.tag=8190;self.slot=254;self.pending_w1c=None;self.isr=0;self.acks=[]
  for i,v in ((2,BAR|0xa0000000),(80,TABLE),(81,254),(82,8190),(83,254),(134,BD),(177,258)):self.put(MB+i*4,v)
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
 def call(self):
  for reg,v in ((UC_MIPS_REG_A0,MB|0xa0000000),(UC_MIPS_REG_SP,(B+0x1fff0)|0xa0000000),(UC_MIPS_REG_RA,0x9fc0fff0)):self.u.reg_write(reg,v)
  self.u.emu_start(symbols['pe_rxq'],0x9fc0fff0,count=100000)
  assert self.u.reg_read(UC_MIPS_REG_PC)==0x9fc0fff0
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

# A preceding ARM exception cannot be overtaken by a protected data frame.
n=Machine();peer=bytes.fromhex('067afb78b287');ap=bytes.fromhex('02e04c076805');dst=bytes.fromhex('0050c2010200')
n.u.mem_write(MB+64,ap);n.u.mem_write(B+0xe410,peer)
n.put(B+0x11a08,1);n.put(ST+0x10008,1<<31|1<<8);n.put(ST+1024,1<<31|0x123)
for i,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST)):n.put(MB+i*4,v)
def data_frame(seq,pn):
 hdr=struct.pack('<HH',0x4188,0)+ap+peer+dst+struct.pack('<H',seq<<4)+bytes(2)
 iv=bytes([pn&255,pn>>8,0,0x20,0,0,0,0]);ip=bytearray(20);ip[0]=0x45;ip[9]=17
 return hdr+iv+bytes.fromhex('aaaa030000000800')+ip+bytes(8)+bytes(12)
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
print(json.dumps({'scope':'Actual linked production PE; modeled PCIe, ARM acknowledgement, NI consumption and W1C. No RF throughput simulation.',
 'PE_sha256':hashlib.sha256(binary).hexdigest(),'ARM_blocked_capture_and_refill_packets':255,'busy_ring_full_payload_preserved':True,
 'ordered_ARM_delivery':len(seen),'additional_wrap_packets':513,'all_514_buffers_have_one_owner':True,'permuted_noncontiguous_DMA_table_all_buffers':True,
 'PN_SN_no_overtake':True,'stateless_reports_and_RTS_CTS_ACK_BA_do_not_block_data':True,'BAR_PS_Poll_wait_without_forcing_later_data_to_ARM':True,'NI_last_two_payloads_retained':True,'NI_end_bit30_and_31bit_wrap':True,'RXQ_RPQ_RDU_counted_with_W1C':True},indent=2))
