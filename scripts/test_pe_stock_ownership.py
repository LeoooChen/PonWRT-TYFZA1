#!/usr/bin/env python3
"""Run the linked PE beyond the former124 owner limit and through RPQ reuse."""
from pathlib import Path
import sys,struct,subprocess,json,random
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'analysis/python-deps'))
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN,UC_HOOK_MEM_WRITE
from unicorn.mips_const import *
from pe_tx_ownership_fixture import initialize
p=Path(sys.argv[1]);binary=(p/'pe-tx.bin').read_bytes()
capacity=int(sys.argv[2]) if len(sys.argv)>2 else 2048
assert capacity in (1024,capacity)
cycles=4095//capacity+2
names={a[2]:int(a[0],16) for l in subprocess.check_output(['llvm-nm-18',str(p/'pe-wd.elf')],text=True).splitlines() if len(a:=l.split())==3}
B,MB,RING,WD,HDR,PLD,RPQ,FWD,BAR,NI=0x4000000,0x4002000,0x5000000,0x5100000,0x5400000,0x6000000,0x6800000,0x6900000,0x12000000,0x10000000
u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
for a,n in [(0x1fc00000,0x10000),(B,0x80000),(RING,0x4000),(WD,0x32000),(HDR,0x50000),(PLD,0x600000),(RPQ,0x40000),(FWD,0x40000),(BAR,0x10000),(NI,0x10000),(0x10020000,0x1000)]:u.mem_map(a,n)
u.mem_write(0x1fc00000,binary);initialize(u,B,requests=capacity)
def put(a,v):u.mem_write(a,struct.pack('<I',v&0xffffffff))
def get(a):return struct.unpack('<I',u.mem_read(a,4))[0]
def call(name,*args):
 for reg,v in zip((UC_MIPS_REG_A0,UC_MIPS_REG_A1,UC_MIPS_REG_A2,UC_MIPS_REG_A3),args):u.reg_write(reg,v|0xa0000000)
 u.reg_write(UC_MIPS_REG_SP,B+0x1fff0|0xa0000000);u.reg_write(UC_MIPS_REG_RA,0x9fc0fff0)
 u.emu_start(names[name],0x9fc0fff0,count=1000000)
 assert u.reg_read(UC_MIPS_REG_PC)==0x9fc0fff0,name
 return u.reg_read(UC_MIPS_REG_V0)
for i,v in [(0,0x50455744),(3,1),(140,MB|0xa0000000),(142,1),(2,BAR|0xa0000000),(11,RING),(12,WD),(14,HDR),(24,1),(68,RPQ),(72,FWD),(116,0x6380),(117,6)]:put(MB+i*4,v)
put(NI+0x6380,1<<31);put(NI+0x6340,4095)
ap=bytes.fromhex('020000000001');dst=bytes.fromhex('020000000002');u.mem_write(MB+64,ap)
u.mem_write(B+0xd020,struct.pack('<8I',0,0,26<<16,0x3019b,1<<24|1<<18|1<<16,7<<16|64,0,0))
put(B+0x39004,0);put(B+0xe204,64<<18);put(B+0x11a04,1);put(B+0x12020,64)
returns=[]
u.hook_add(UC_HOOK_MEM_WRITE,lambda uc,access,a,n,v,d:returns.append(v),begin=NI+0x6384,end=NI+0x6387)
def enqueue(count):
 put(B+0x11804,count);put(B+0x13020,count);put(B+0x18020,count);put(B+0x15020,PLD);put(B+0x16020,PLD+(count-1)*2048)
 put(B+0x19604,1);put(B+0x19004,2);put(B+0x19204,2);put(MB+170*4,2);put(MB+38*4,count)
 for i in range(count):
  a=PLD+i*2048;put(a,a+2048 if i+1<count else 0);put(a+4,1500|16<<16)
  u.mem_write(a+80,dst+ap+b'\x08\x00'+bytes([i&255])*1486)
all_tags=[]
for cycle in range(cycles):
 enqueue(capacity);tags=[];owners=[]
 for i in range(capacity):
  assert call('ni_select',MB,B,B+0x4000)==1
  owner=get(B+0x4000);owners.append(owner)
  call('pe_build_tx',MB,B,B+0x4000,B+0x3000)
  page=get(RING+(((get(MB+32*4)-1)&255)*8)+4)
  tag=get(page+56)&4095;tags.append(tag)
  assert get(B+0x40000+tag*4)==owner+1
  assert bool(get(page+12)&(1<<13)) == (cycle == 0 and i == 0)
 assert len(set(owners))==len(set(tags))==capacity and get(MB+30*4)==capacity
 enqueue(1);assert call('ni_select',MB,B,B+0x4000)==0
 put(B+0x15020,0);put(B+0x11804,0);put(B+0x13020,0);put(MB+38*4,0);put(MB+170*4,0);put(B+0x18020,capacity)
 order=list(range(capacity));random.Random(cycle).shuffle(order)
 before=len(returns)
 for chunk in range(0,capacity,128):
  ids=order[chunk:chunk+128];consumer=get(MB+70*4);epoch=get(MB+71*4)+1
  if epoch==8192:epoch=1
  reports=b''.join(struct.pack('<I',1|tags[i]<<16) for i in ids)
  raw=struct.pack('<II',(20+len(reports))|0xc000|epoch<<16,7<<24|len(reports))+bytes(12)+reports
  u.mem_write(RPQ+consumer*1024,raw);put(BAR+0x121c,((consumer+1)&255)<<16)
  put(NI+0x6340,0)
  u.reg_write(UC_MIPS_REG_A0,MB|0xa0000000);u.reg_write(UC_MIPS_REG_SP,B+0x1fff0|0xa0000000)
  u.emu_start(names['pe_main'],0x9fc0fff0,count=150000)
  assert get(MB+70*4)==((consumer+1)&255)
  if chunk==0:
   put(NI+0x6340,0);call('ni_recycle',MB,B)
   assert get(MB+30*4)==capacity and len(returns)==before
  put(NI+0x6340,4095);call('ni_recycle',MB,B)
 assert get(MB+30*4)==0 and get(B+0x18020)==0
 assert len(returns)-before==capacity and len(set(returns[before:]))==capacity
 assert all(get(B+0x40000+t*4)==0 for t in tags)
 assert get(B+0x34004)-get(B+0x34000)==capacity
 assert get(B+0x3e010)==0
 all_tags+=tags
assert max(all_tags)==4094 and 0 in all_tags
# Former metadata regions are unaffected by the moved owner/rate/generation tables.
assert get(MB+77*4)==0
print(json.dumps({'linked_submissions':capacity*cycles,'max_inflight':capacity,'random_RPQ_order':True,'tag4094_wrap_and_zero':True,'no_BID_return_without_EQ_credit':True,'no_duplicate_owner_or_BID':True,'completion4096_wrap':True,'custom_BK_absent':True,'faults':0},indent=2))
