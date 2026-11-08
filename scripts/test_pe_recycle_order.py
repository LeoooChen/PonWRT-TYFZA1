#!/usr/bin/env python3
"""Exercise actual seven-BID recycle order, partial returns and immediate reuse."""
import json,struct,subprocess,sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "analysis/python-deps"))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_SP, UC_MIPS_REG_RA
p=Path(sys.argv[1]); symbols={x[2]:int(x[0],16) for l in subprocess.check_output(['llvm-nm-18',str(p/'pe-wd.elf')],text=True).splitlines() if len(x:=l.split())==3}
BASE,MB,NI,BUF=0x04000000,0x04002000,0x10000000,0x06000000

def check(credit,ready_after,discard):
 u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
 for a,n in [(0x1fc00000,0x10000),(BASE,0x20000),(NI,0x10000),(BUF,0x4000),(0x07000000,0x1000)]:u.mem_map(a,n)
 u.mem_write(0x1fc00000,(p/'pe-tx.bin').read_bytes())
 def put(a,v):u.mem_write(a,struct.pack('<I',v))
 def get(a):return struct.unpack('<I',u.mem_read(a,4))[0]
 for i,v in [(116,0x6380),(117,6),(30,1),(45,0),(46,127),(47,1 if discard else 0)]:put(MB+i*4,v)
 put(BASE+0xc800+127*4,0);put(BASE+0xc400,BUF);put(BASE+0xc404,1<<31)
 for i in range(7):put(BUF+i*2048,BUF+(i+1)*2048 if i<6 else 0)
 put(NI+0x6340,credit);put(NI+0x6380,1<<31)
 events=[];returned=set();last_read=None;needs_sync=False
 def code(uc,at,size,data):
  nonlocal needs_sync
  if get(at&0x1fffffff)==15:events.append('sync');needs_sync=False
 def read(uc,access,at,size,value,data):
  nonlocal last_read
  a=at&0x1fffffff
  if BUF<=a<BUF+7*2048:
   assert a not in returned,('read returned BID',a)
   last_read=a;events.append('next')
 def write(uc,access,at,size,value,data):
  nonlocal needs_sync
  a=at&0x1fffffff
  if a==NI+0x6384:
   b=value&~127
   assert b==last_read and b not in returned
   assert get(NI+0x6340)>0 and get(NI+0x6380)&(1<<31)
   returned.add(b);put(NI+0x6340,get(NI+0x6340)-1);put(b,0xdeadbeef)
   events.append('push');needs_sync=True
   if len(returned)==ready_after:put(NI+0x6380,0)
  elif a in (BASE+0xc400,BASE+0xc404,MB+46*4,MB+47*4):
   assert not needs_sync,'owner publication before post-PUSH SYNC'
 u.hook_add(UC_HOOK_CODE,code);u.hook_add(UC_HOOK_MEM_READ,read);u.hook_add(UC_HOOK_MEM_WRITE,write)
 def call():
  u.reg_write(UC_MIPS_REG_A0,MB|0xa0000000);u.reg_write(UC_MIPS_REG_A1,BASE|0xa0000000)
  u.reg_write(UC_MIPS_REG_SP,BASE+0x1fff0|0xa0000000);u.reg_write(UC_MIPS_REG_RA,0x87000000)
  u.emu_start(symbols['ni_recycle'],0x87000000,count=10000)
 call();first=len(returned);syncs=events.count('sync')
 expected=min(7,credit,ready_after)
 assert first==expected
 if first<7:
  assert get(BASE+0xc400)==BUF+first*2048 and get(MB+30*4)==1
  put(NI+0x6340,7);put(NI+0x6380,1<<31);call()
 assert len(returned)==7 and get(BASE+0xc400)==0 and get(MB+30*4)==0
 assert get(MB+29*4)==7 and get(MB+47*4)==0
 if not discard:assert get(MB+46*4)==0
 return syncs
cases = [(7,99,False),(3,99,False),(7,3,False),(7,99,True),(0,99,True)]
sync_counts = [check(*case) for case in cases]
assert sync_counts == [8,4,4,9,1], sync_counts
print(json.dumps({'sync_counts_by_case':sync_counts,'seven_BID_whole_and_partial_return':True,
                  'next_before_PUSH_and_post_PUSH_fence':True,
                  'wrap_discard_and_immediate_reuse':True},indent=2))
