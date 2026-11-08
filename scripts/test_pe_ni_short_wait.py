#!/usr/bin/env python3
"""Execute linked PE short-frame aggregation, deadline and peer isolation."""
from pathlib import Path
from pe_tx_ownership_fixture import initialize
import sys,struct,subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"analysis/python-deps"))
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_A1,UC_MIPS_REG_A2,UC_MIPS_REG_SP,UC_MIPS_REG_RA,UC_MIPS_REG_V0
p=Path(sys.argv[1])
symbols={x[2]:int(x[0],16) for l in subprocess.check_output(["llvm-nm-18",str(p/"pe-wd.elf")],text=True).splitlines() if len(x:=l.split())==3}
u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
BASE,MB,NI,PAYLOAD,EPP,REQ=0x04000000,0x04002000,0x10000000,0x06000000,0x05000000,0x04007000
for a,n in [(0x1fc00000,0x10000),(BASE,0x80000),(NI,0x10000),(PAYLOAD,0x200000),(0x12000000,0x10000),(EPP,0x40000),(0x10020000,0x1000)]:u.mem_map(a,n)
u.mem_write(0x1fc00000,(p/"pe-tx.bin").read_bytes())
initialize(u,BASE)
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
def enqueue(n,peer,size,tos=0):
 frame=mac[peer-1]+bytes.fromhex("0200000000030800")+bytes([0x45,tos])+bytes(size-16)
 at=PAYLOAD+n*2048
 u.mem_write(at+64,struct.pack(">II",0x80000000,(len(frame)+8)<<15)+bytes(8)+frame)
 u.mem_write(EPP+n*8,struct.pack("<II",at|0x50,len(frame)+16))
 put(NI+0x7060,(n+1)*8);call('ni_ingest')
put(BASE+0x39004,3793 | 2<<16)
put(BASE+0x39008,0)
enqueue(0,1,66)
assert call('ni_select')==0
# Disabled aggregation has no artificial short-frame delay, isolated per peer.
enqueue(1,2,66)
assert call('ni_select')==1 and get(REQ+32)==PAYLOAD+2048+80
put(0x1200c438,10999);assert call('ni_select')==0
put(0x1200c438,11000);assert call('ni_select')==1
# Size mode holds eight small frames and releases nine.
for n in range(2,10):enqueue(n,1,66)
assert call('ni_select')==0
enqueue(10,1,66)
assert call('ni_select')==9 and get(REQ+8)>>24==9
# Factory shorter timer is a policy decision; timer subtraction wraps.
put(BASE+0x39004,3793 | 2<<16 | 1<<25)
put(0x1200c438,0xffffffb0);enqueue(11,1,66)
put(0x1200c438,3900);assert call('ni_select')==0
put(0x1200c438,3920);assert call('ni_select')==1
# Retirement and a PS grant bypass aggregation immediately.
enqueue(12,1,66);put(BASE+0x11a04,0)
assert call('ni_select')==0 and get(MB+47*4)!=0 and get(BASE+0x11804)==0
assert get(BASE+0x3a080)==0 and get(BASE+0x3a088)==0
put(BASE+0x11a04,1);put(MB+47*4,0)
put(BASE+0xe800+16,1);put(BASE+0x19e04,2)
put(BASE+0xe800+20,1);put(BASE+0xe800+28,1<<16)
enqueue(13,1,66)
assert call('ni_select')==1 and get(BASE+0x11804)==0
put(BASE+0xe800+16,0)
# Batch mode starts at128 then drains while at least the policy count remains.
put(BASE+0x39004,10896 | 7<<16 | 1<<24)
put(BASE+0xe204,10896<<18)
put(0x1200c438,20000)
for n in range(14,141):enqueue(n,1,1514)
assert get(BASE+0x13020)==127 and call('ni_select')==0
enqueue(141,1,1514)
assert call('ni_select')==7
for n in range(17):assert call('ni_select')==7
assert get(BASE+0x13020)==2 and call('ni_select')==0
put(0x1200c438,30000);assert call('ni_select')==2
assert get(BASE+0x3a080)==0
# Per-AC quota counts accepted MPDUs, including ARM requests.
put(BASE+0x39004,0);put(BASE+0x3f000,2)
put(BASE+0x3e010,1);put(BASE+0x3e810,1)
before=get(MB+30*4)
enqueue(142,1,66)
assert call('ni_select')==0 and get(MB+47*4)==2049
assert get(BASE+0x3e010)==1 and get(BASE+0x3f004)==1
# A rejected request retains its BID until EQ credit allows recycling.
put(MB+116*4,0x6380);put(MB+117*4,6)
put(NI+0x6380,1<<31);put(NI+0x6340,0)
call('ni_recycle');assert get(MB+47*4)==2049
put(NI+0x6340,8);call('ni_recycle')
assert get(MB+47*4)==0 and get(BASE+0x3e010)==1 and get(MB+30*4)==before
# Shared AC full does not reject an independent peer.
enqueue(143,2,66);assert call('ni_select')==1
put(BASE+0x3e810,0);enqueue(144,1,66)
assert call('ni_select')==1 and get(BASE+0x3e010)==2
# NI payload classification preserves a separate VI TID despite EPP COS0.
put(BASE+0x3f000,0);enqueue(145,1,66,0x80)
assert get(BASE+0x13000+(8+4)*4)==1
assert call('ni_select')==1 and get(REQ+4)==2 and get(REQ+16)&7==4
print('PASS linked PE: SWQ gates/timers/PS and WMM per-AC quota,ARM accounting,drop ownership,peer isolation')
