#!/usr/bin/env python3
"""Check linked multi-MPDU credit snapshots, exhaustion and partial return."""
from pathlib import Path
import sys,struct,subprocess,json
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN,UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_A1,UC_MIPS_REG_SP,UC_MIPS_REG_RA
p=Path(sys.argv[1]);sym={x[2]:int(x[0],16) for l in subprocess.check_output(['llvm-nm-18',str(p/'pe-wd.elf')],text=True).splitlines() if len(x:=l.split())==3}
B,M,N,F=0x04000000,0x04002000,0x10000000,0x06000000
def run(credit,busy,replenish):
 u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
 for a,n in [(0x1fc00000,0x10000),(B,0x20000),(N,0x10000),(F,0x20000),(0x07000000,0x1000)]:u.mem_map(a,n)
 u.mem_write(0x1fc00000,(p/'pe-tx.bin').read_bytes())
 def put(a,v):u.mem_write(a,struct.pack('<I',v))
 def get(a):return struct.unpack('<I',u.mem_read(a,4))[0]
 for i,v in [(116,0x6380),(117,6),(30,16),(45,14),(46,126)]:put(M+4*i,v)
 for i in range(16):
  put(B+0xc800+((126+i)&127)*4,i);put(B+0xc400+i*8,F+i*7*128);put(B+0xc404+i*8,1<<31)
  for k in range(7):put(F+(i*7+k)*128,F+(i*7+k+1)*128 if k<6 else 0)
 put(N+0x6340,credit);put(N+0x6380,1<<31)
 counts=[0,0];sent=[]
 def read(uc,access,at,size,value,data):
  if at==N+0x6340:counts[0]+=1
  if at==N+0x6380:counts[1]+=1
 def write(uc,access,at,size,value,data):
  if at!=N+0x6384:return
  assert get(N+0x6340)>0 and get(N+0x6380)&(1<<31)
  assert value&~127==F+len(sent)*128
  sent.append(value);put(N+0x6340,get(N+0x6340)-1);put(value&~127,0xdeadbeef)
  if len(sent)==busy:put(N+0x6380,0)
  if len(sent)==replenish:put(N+0x6340,get(N+0x6340)+112)
 u.hook_add(UC_HOOK_MEM_READ,read);u.hook_add(UC_HOOK_MEM_WRITE,write)
 def call():
  u.reg_write(UC_MIPS_REG_A0,M|0xa0000000);u.reg_write(UC_MIPS_REG_A1,B|0xa0000000);u.reg_write(UC_MIPS_REG_SP,B+0x1fff0|0xa0000000);u.reg_write(UC_MIPS_REG_RA,0x87000000);u.emu_start(sym['ni_recycle'],0x87000000,count=100000)
 call();first=(len(sent),counts[:])
 assert get(M+29*4)==len(sent) and get(M+30*4)==16-len(sent)//7
 if len(sent)<112:put(N+0x6340,112);put(N+0x6380,1<<31);call()
 assert len(sent)==112 and get(M+30*4)==0 and get(M+46*4)==14
 return first
rows=[]
for credit,busy,replenish,expected in [(112,999,999,112),(7,999,7,112),(5,999,999,5),(112,17,999,17),(0,999,999,0),(112,7,999,7),(10,999,7,10)]:
 result=run(credit,busy,replenish)
 assert result[0]==expected,result
 rows.append({'credit':credit,'busy_after':busy,'refill_after':replenish,'first_call':result})
assert rows[0]['first_call'][1]==[1,112]
assert rows[1]['first_call'][1]==[2,112]
print(json.dumps(rows,indent=2))
