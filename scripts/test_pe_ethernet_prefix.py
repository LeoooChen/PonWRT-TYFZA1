#!/usr/bin/env python3
"""Compare old/new linked PE Ethernet classification and flow keys, including DDR reads.
Usage: test_pe_ethernet_prefix.py OLD_PE_DIRECTORY NEW_PE_DIRECTORY
"""
from pathlib import Path
import sys,struct,subprocess
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'analysis/python-deps'),str(root/'scripts')]
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN,UC_HOOK_MEM_READ
from unicorn.mips_const import *
from pe_tx_ownership_fixture import initialize
B,MB,NI,BAR,EPP,BUF,REQ=0x4000000,0x4002000,0x10000000,0x12000000,0x5000000,0x6000000,0x4007000
mac=bytes.fromhex('020000000001');src=bytes.fromhex('123456789abc')
class Machine:
 def __init__(self,path):
  p=Path(path);self.path=p;self.u=u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
  for a,n in [(0x1fc00000,0x10000),(B,0x80000),(NI,0x10000),(BAR,0x10000),(EPP,0x1000),(BUF,0x1000)]:u.mem_map(a,n)
  u.mem_write(0x1fc00000,(p/'pe-tx.bin').read_bytes());self.entry=next(int(l.split()[0],16) for l in subprocess.check_output(['llvm-nm-18',str(p/'pe-wd.elf')],text=True).splitlines() if l.endswith(' ni_ingest'))
  self.reads=0
  def read(*a):self.reads+=1
  u.hook_add(UC_HOOK_MEM_READ,read,begin=BUF+64,end=BUF+128)
 def put(self,a,v):self.u.mem_write(a,struct.pack('<I',v))
 def run(self,packet,header):
  u=self.u;put=self.put;u.mem_write(B,bytes(0x80000));initialize(u,B);u.mem_write(BUF,bytes(4096))
  for i,v in [(2,BAR|0xa0000000),(25,EPP),(114,0x7060),(115,0x7160),(167,15)]:put(MB+i*4,v)
  put(NI+0x659c,1<<24);put(NI+0x7060,8);u.mem_write(B+0xe408,mac);put(B+0x11a04,1);put(B+0xd028,26<<16)
  u.mem_write(BUF+64+header,packet)
  if header==16:put(EPP+4,1<<14|len(packet)+16)
  else:
   put(EPP+4,0);u.mem_write(BUF+64,struct.pack('>II',0,(len(packet))<<15))
  put(EPP,BUF|0x56);self.reads=0
  for reg,v in [(UC_MIPS_REG_A0,MB),(UC_MIPS_REG_A1,B),(UC_MIPS_REG_SP,B+0x1fff0)]:u.reg_write(reg,v|0xa0000000)
  u.reg_write(UC_MIPS_REG_RA,0x9fc0fff0);u.emu_start(self.entry,0x9fc0fff0,count=100000)
  assert u.reg_read(UC_MIPS_REG_PC)==0x9fc0fff0
  return bytes(u.mem_read(B+0x13020,32))+bytes(u.mem_read(B+0x60100,256))+bytes(u.mem_read(BUF,20))
a,b=[Machine(p) for p in sys.argv[1:]];count=0
for proto in [0x0800,0x86dd,0x21,0x57]:
 for tos in range(256):
  ip=bytes([0x45,tos]) if proto in [0x800,0x21] else bytes([0x60|tos>>4,(tos&15)<<4])
  packet=mac+src+struct.pack('>H',proto if proto>255 else 0x8864)
  if proto<256:packet+=bytes.fromhex('110000010028')+struct.pack('>H',proto)
  packet+=ip+bytes(48)
  for head in [8,16]:
   assert a.run(packet,head)==b.run(packet,head),(proto,tos,head)
   count+=1
print('PASS',count,'linked NI IPv4/IPv6/PPPoE classifications,flow keys,metadata; both NI headers')
packet=mac+src+b'\x08\x00\x45\x00'+bytes(48)
a.run(packet,16);b.run(packet,16);print('Ethernet prefix DDR reads:',a.reads,'->',b.reads)

for size in [14,15,16]:
 packet=mac+src+b'\x08\x00'+bytes(max(0,size-14))
 for head in [8,16]:assert a.run(packet,head)==b.run(packet,head),(size,head)
print('Short14/15/16-byte frames preserve classification and key')
