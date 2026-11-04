#!/usr/bin/env python3
"""Execute linked PE short-frame aggregation, deadline and peer isolation."""
from pathlib import Path
import sys,struct,subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"analysis/python-deps"))
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_A1,UC_MIPS_REG_A2,UC_MIPS_REG_SP,UC_MIPS_REG_RA,UC_MIPS_REG_V0
p=Path(sys.argv[1])
symbols={x[2]:int(x[0],16) for l in subprocess.check_output(["llvm-nm-18",str(p/"pe-wd.elf")],text=True).splitlines() if len(x:=l.split())==3}
u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
BASE,MB,NI,PAYLOAD,EPP,REQ=0x04000000,0x04002000,0x10000000,0x06000000,0x05000000,0x04007000
for a,n in [(0x1fc00000,0x10000),(BASE,0x20000),(NI,0x10000),(PAYLOAD,0x200000),(0x12000000,0x10000),(EPP,0x40000),(0x10020000,0x1000)]:u.mem_map(a,n)
u.mem_write(0x1fc00000,(p/"pe-tx.bin").read_bytes())
def put(a,v):u.mem_write(a,struct.pack("<I",v))
def get(a):return struct.unpack("<I",u.mem_read(a,4))[0]
for i,v in [(25,EPP),(114,0x7060),(115,0x7160),(167,15)]:put(MB+i*4,v)
put(NI+0x659c,1<<24)
mac=[bytes.fromhex("020000000001"),bytes.fromhex("020000000002")]
for peer in [1,2]:
 put(BASE+0x11a00+peer*4,1);put(BASE+0xe200+peer*4,3839<<18|1<<17)
 u.mem_write(BASE+0xe400+peer*8,mac[peer-1])
 u.mem_write(BASE+0xd000+peer*32,struct.pack("<8I",0,0,26<<16,0x20100,peer<<24|1<<18|1<<16,3<<16|64,0,0))
 put(BASE+0x12000+peer*8*4,64)
 put(BASE+0x19800+peer*4,10)
def call(name):
 u.reg_write(UC_MIPS_REG_A0,MB|0xa0000000);u.reg_write(UC_MIPS_REG_A1,BASE|0xa0000000);u.reg_write(UC_MIPS_REG_A2,REQ|0xa0000000)
 u.reg_write(UC_MIPS_REG_SP,(BASE+0x1fff0)|0xa0000000);u.reg_write(UC_MIPS_REG_RA,0x9fc01ff0)
 u.emu_start(symbols[name],0x9fc01ff0,count=100000)
 return u.reg_read(UC_MIPS_REG_V0)

put(MB+2*4,0xb2000000)
put(0x1200c438,1000)
def enqueue(n,peer,size):
 frame=mac[peer-1]+bytes.fromhex("0200000000030800")+bytes(size-14)
 at=PAYLOAD+n*2048
 u.mem_write(at+64,struct.pack(">II",0x80000000,(len(frame)+8)<<15)+bytes(8)+frame)
 u.mem_write(EPP+n*8,struct.pack("<II",at|0x50,len(frame)+16))
 put(NI+0x7060,(n+1)*8);call('ni_ingest')
enqueue(0,1,66)
assert get(PAYLOAD+12)==1000
assert call('ni_select')==0 and get(MB+30*4)==0
# A different peer's large frame proceeds while the short-frame queue waits.
enqueue(1,2,1500)
assert call('ni_select')==1 and get(REQ+32)==PAYLOAD+2048+80
put(0x1200c438,4999);assert call('ni_select')==0
put(0x1200c438,5000);assert call('ni_select')==1
# Eight small frames release immediately and remain a single aggregate.
for n in range(2,10):enqueue(n,1,66)
assert call('ni_select')==8 and get(REQ+8)>>24==8
# Unsigned TSF subtraction expires correctly across wrap.
put(0x1200c438,0xffffffb0);enqueue(10,1,66)
put(0x1200c438,3900);assert call('ni_select')==0
put(0x1200c438,3920);assert call('ni_select')==1
# Retirement and a PS grant bypass batching immediately.
enqueue(11,1,66);put(BASE+0x11a04,0)
assert call('ni_select')==0 and get(MB+47*4)!=0 and get(BASE+0x11804)==0
put(BASE+0x11a04,1);put(MB+47*4,0)
put(BASE+0xe800+16,1);put(BASE+0x19e04,2)
put(BASE+0xe800+20,1);put(BASE+0xe800+28,1<<16)
enqueue(12,1,66)
assert call('ni_select')==1 and get(BASE+0x11804)==0
print('PASS linked PE: short-frame wait, peer isolation, full aggregate, TSF wrap, retirement and PS grant')
