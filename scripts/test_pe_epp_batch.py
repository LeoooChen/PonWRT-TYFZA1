#!/usr/bin/env python3
"""Execute bounded EPP batches across COS, TID groups, wrap and a retire cutoff."""
from pathlib import Path
import sys,struct,subprocess,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"analysis/python-deps"))
from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE,Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_A1,UC_MIPS_REG_A2,UC_MIPS_REG_SP,UC_MIPS_REG_RA,UC_MIPS_REG_V0
p=Path(sys.argv[1])
symbols={x[2]:int(x[0],16) for l in subprocess.check_output(["llvm-nm-18",str(p/"pe-wd.elf")],text=True).splitlines() if len(x:=l.split())==3}
u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
BASE,MB,NI,PAYLOAD,EPP=0x04000000,0x04002000,0x10000000,0x06000000,0x05000000
for a,n in [(0x1fc00000,0x10000),(BASE,0x20000),(NI,0x10000),(PAYLOAD,0x200000),(0x12000000,0x10000),(EPP,0x80000),(0x10020000,0x1000)]:u.mem_map(a,n)
u.mem_write(0x1fc00000,(p/"pe-tx.bin").read_bytes())
def put(a,v):u.mem_write(a,struct.pack("<I",v))
def get(a):return struct.unpack("<I",u.mem_read(a,4))[0]
for i,v in [(25,EPP),(114,0x7060),(115,0x7160),(167,15)]:put(MB+i*4,v)
put(NI+0x659c,1<<24)
mac=[bytes.fromhex("020000000001"),bytes.fromhex("020000000002")]
for peer in [1,2]:
 put(BASE+0x11a00+peer*4,1)
 u.mem_write(BASE+0xe400+peer*8,mac[peer-1])
def call(name):
 u.reg_write(UC_MIPS_REG_A0,MB|0xa0000000);u.reg_write(UC_MIPS_REG_A1,BASE|0xa0000000)
 u.reg_write(UC_MIPS_REG_A2,(BASE+0x7000)|0xa0000000)
 u.reg_write(UC_MIPS_REG_SP,(BASE+0x1fff0)|0xa0000000);u.reg_write(UC_MIPS_REG_RA,0x9fc01ff0)
 u.emu_start(symbols[name],0x9fc01ff0,count=100000)

# Wrapped EPP batch: consecutive groups, interleaved peers/TIDs and compact EPP.
rows=[(1,0),(1,0),(2,3),(1,0),(2,3),(2,3),(1,7),(1,7),(2,0)] + [(1,0),(2,3)] * 4
start=32752
for n,(peer,tid) in enumerate(rows):
 at=PAYLOAD+n*2048
 frame=mac[peer-1]+bytes.fromhex("0200000000030800")+bytes(50)
 u.mem_write(at+64,struct.pack(">II",0x80000000,(len(frame)+8)<<15|tid)+bytes(8)+frame)
 u.mem_write(EPP+((start+n*8)&32767),struct.pack("<II",at|0x50,1<<14|len(frame)+16))
put(BASE+0x11c00,start);put(NI+0x7060,(start+len(rows)*8)&32767)
put(BASE+0x19604,255)
put(MB+171*4,1);put(BASE+0x19400,(start+3*8)&32767)
writes=[]
watch=u.hook_add(UC_HOOK_MEM_WRITE,lambda uc,access,at,size,value,data:writes.append((at,value)))
call("ni_ingest")
assert get(NI+0x7160)==112 and get(MB+171*4)==0
assert next(i for i,(at,v) in enumerate(writes) if at==NI+0x7160)<next(i for i,(at,v) in enumerate(writes) if at==MB+171*4)
assert get(MB+38*4)==16 and get(BASE+0x11804)==sum(peer==1 for peer,tid in rows[:16]) and get(BASE+0x11808)==sum(peer==2 for peer,tid in rows[:16])
call("ni_ingest")
for peer,tid in set(rows):
 queue=peer*8+tid
 expected=[PAYLOAD+n*2048 for n,p in enumerate(rows) if p==(peer,tid)]
 assert get(BASE+0x13000+queue*4)==len(expected)
 assert get(BASE+0x18000+queue*4)==len(expected)
 node=get(BASE+0x15000+queue*4);seen=[]
 while node:
  seen.append(node);node=get(node)
 assert seen==expected
 assert get(BASE+0x16000+queue*4)==expected[-1]
# COS selection and an invalid next entry must not publish an unconsumed BID.
put(NI+0x659c,0x81<<24);put(NI+0x707c,8)
at=PAYLOAD+20*2048
u.mem_write(at+64,struct.pack(">II",0x80000000,72<<15|2)+bytes(8)+mac[1]+bytes.fromhex("0200000000030800")+bytes(50))
u.mem_write(EPP+(7<<15),struct.pack("<II",at|0x50,80))
call("ni_ingest"); assert get(NI+0x717c)==8
put(NI+0x707c,16)
call("ni_ingest"); assert get(NI+0x717c)==8
# The write observer is only needed for ingestion's RD/fence ordering.
u.hook_del(watch)
# Compare sparse active-TID selection with the original round-robin search.
assert get(BASE+0x19604)==129 and get(BASE+0x19608)==13
for peer in [1,2]:
 u.mem_write(BASE+0xd000+peer*32,struct.pack('<8I',0,0,26<<16,0,peer<<24,0,0,0))
pending={key:[PAYLOAD+n*2048 for n,key2 in enumerate(rows) if key2==key] for key in set(rows)}
pending[2,2]=[at]
peers=[1,2];cursor=1;put(MB+112*4,cursor)
while peers:
 peer=peers.pop(0)
 tid=next((cursor+i)&7 for i in range(8) if pending.get((peer,(cursor+i)&7)))
 expected=pending[peer,tid].pop(0)
 call('ni_select')
 assert u.reg_read(UC_MIPS_REG_V0)==1
 assert get(BASE+0x7000+32)==expected+80
 assert get(BASE+0x7000+16)&7==tid
 cursor=(tid+1)&7
 mask=sum(1<<t for t in range(8) if pending.get((peer,t)))
 assert get(BASE+0x19600+peer*4)==mask
 if mask:peers.append(peer)
assert get(MB+38*4)==0

def lookup_probe(withdraw, compact=True):
 u.mem_write(BASE,bytes(0x20000));u.mem_write(EPP,bytes(64))
 for i,v in [(25,EPP),(114,0x7060),(115,0x7160),(167,15)]:put(MB+i*4,v)
 put(NI+0x659c,1<<24);put(NI+0x7060,64)
 for peer in (1,2):
  put(BASE+0x11a00+peer*4,1);u.mem_write(BASE+0xe400+peer*8,mac[peer-1])
 frames=[]
 link_writes=[]
 for n in range(8):
  at=PAYLOAD+n*2048;frame=mac[1]+bytes.fromhex('0200000000030800')+bytes([n])*50
  put(at,0xdeadbe80)
  frames.append(frame);u.mem_write(at+64,struct.pack('>II',0x80000000,72<<15)+bytes(8)+frame)
  u.mem_write(EPP+n*8,struct.pack('<II',at|0x50,(1<<14 if compact else 0)|80))
 reads={'route':0,'address':0,'EPP_head':0,'EPP_base':0,'clock':0,'header1':0};revoked=False
 def observe(uc,access,at,size,value,data):
  nonlocal revoked
  at&=0x1fffffff
  if BASE+0x11a00<=at<BASE+0x11c00:reads['route']+=1
  if BASE+0xe400<=at<BASE+0xe800:reads['address']+=1
  if EPP<=at<EPP+64 and not (at-EPP)%8:reads['EPP_head']+=1
  if at==MB+25*4:reads['EPP_base']+=1
  if at==MB+195*4:reads['clock']+=1
  if PAYLOAD<=at<PAYLOAD+8*2048 and (at-PAYLOAD)%2048==68:reads['header1']+=1
  # Admission can be withdrawn before ARM has paused the PE.
  if withdraw and at==EPP+32 and not revoked:
   put(BASE+0x11a08,0);revoked=True
 hook=u.hook_add(UC_HOOK_MEM_READ,observe)
 def link_write(uc,access,at,size,value,data):
  at&=0x1fffffff
  if PAYLOAD<=at<PAYLOAD+8*2048 and not (at-PAYLOAD)%2048:link_writes.append((at,value))
 links=u.hook_add(UC_HOOK_MEM_WRITE,link_write) if not withdraw else None
 call('ni_ingest');u.hook_del(hook)
 if links:
  u.hook_del(links);assert len(link_writes)==8,link_writes
 assert get(NI+0x7160)==64 and get(MB+38*4)==8
 for queue,indices in ({16:range(4),0:range(4,8)} if withdraw else {16:range(8)}).items():
  node=get(BASE+0x15000+queue*4);actual=[]
  while node:actual.append(node);node=get(node)
  assert actual==[PAYLOAD+n*2048 for n in indices]
 for n,frame in enumerate(frames):assert bytes(u.mem_read(PAYLOAD+n*2048+80,64))==frame
 assert get(MB+40*4)==(4 if withdraw else 0)
 return reads

repeated=lookup_probe(False);withdrawn=lookup_probe(True)
normal=lookup_probe(False,False)
if '--before' in sys.argv:assert repeated['address']>=24
else:
 assert repeated['address']<=4
 assert repeated['EPP_head']==8 and repeated['EPP_base']==1 and repeated['clock']==1, repeated
 assert repeated['header1']==normal['header1']==8, (repeated,normal)
print(json.dumps(dict(batch_budget=16,wrapped_ring=True,mixed_peer_TID_FIFO=True,compact_EPP=True,retire_fence_after_queue_and_consumer=True,COS_priority=True,invalid_entry_not_consumed=True,active_TID_round_robin=True,stale_active_mask_reset=True,repeated_peer_DDR_reads=repeated,withdrawn_peer_DDR_reads=withdrawn,mid_batch_admission_withdrawal_preserved=True)))
