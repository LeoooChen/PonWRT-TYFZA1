#!/usr/bin/env python3
"""Compare WP admission using linked old/new PE with identical aggregate queues."""
from pathlib import Path
import hashlib,json,struct,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'analysis/python-deps'))
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_A1,UC_MIPS_REG_A2,UC_MIPS_REG_SP,UC_MIPS_REG_RA,UC_MIPS_REG_PC,UC_MIPS_REG_V0
BASE,MB,NI,EPP,PAYLOAD,REQ=0x4000000,0x4002000,0x10000000,0x5000000,0x6000000,0x4007000
def replay(path,new,msdu_bytes,gather):
 path=Path(path);binary=(path/'pe-tx.bin').read_bytes()
 names={p[2]:int(p[0],16) for l in subprocess.check_output(['llvm-nm-18',str(path/'pe-wd.elf')],text=True).splitlines() if len(p:=l.split())==3}
 u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
 for a,n in ((0x1fc00000,0x10000),(BASE,0x20000),(NI,0x10000),(EPP,0x10000),(PAYLOAD,0x200000)):u.mem_map(a,n)
 u.mem_write(0x1fc00000,binary)
 def put(a,v):u.mem_write(a,struct.pack('<I',v))
 def get(a):return struct.unpack('<I',u.mem_read(a,4))[0]
 for i,v in ((25,EPP),(114,0x7060),(115,0x7160),(167,15)):put(MB+i*4,v)
 mac=bytes.fromhex('020000000001');u.mem_write(BASE+0xe408,mac)
 put(BASE+0x11a04,1);put(BASE+0xe204,7935<<18);put(NI+0x659c,1<<24)
 u.mem_write(BASE+0xd020,struct.pack('<8I',0,0,26<<16,0x30180,1<<24|1<<18|1<<16,7<<16|64,0,0))
 for n in range(1024):
  frame=mac+bytes.fromhex('0200000000020800')+bytes([n&255])*(msdu_bytes-14)
  at=PAYLOAD+n*2048
  u.mem_write(at+32,struct.pack('>II',1<<31,(msdu_bytes+8)<<15)+bytes(8)+frame)
  u.mem_write(EPP+n*8,struct.pack('<II',at|0x50,msdu_bytes+16))
 put(NI+0x7060,1024*8)
 def call(name):
  for reg,v in ((UC_MIPS_REG_A0,MB|0xa0000000),(UC_MIPS_REG_A1,BASE|0xa0000000),(UC_MIPS_REG_A2,REQ|0xa0000000),(UC_MIPS_REG_SP,(BASE+0x1fff0)|0xa0000000),(UC_MIPS_REG_RA,0x9fc0fff0)):u.reg_write(reg,v)
  u.emu_start(names[name],0x9fc0fff0,count=100000)
  assert u.reg_read(UC_MIPS_REG_PC)==0x9fc0fff0
  return u.reg_read(UC_MIPS_REG_V0)
 selected=0;ids=[]
 for batch in range(128):
  for n in range(8):call('ni_ingest')
  frames=call('ni_select')
  if frames:selected+=frames;ids.append(get(REQ)-256)
 owners=[get(BASE+0xc400+n*8) for n in range(128)]
 if new:
  assert len(set(ids))==len(ids)==get(MB+30*4)==124
  buffers=[]
  for id in ids:
   at=owners[id]
   while at:buffers.append(at);at=get(at)
  assert len(buffers)==len(set(buffers))==selected==124*gather
  assert call('ni_select')==0
 else:
  assert selected==124 and len(ids)==(124+gather-1)//gather and get(MB+30*4)==124
 return {'PE_sha256':hashlib.sha256(binary).hexdigest(),'EPP_ingested':get(NI+0x7160)//8,'selected_MSDUs':selected,'actual_MPDU_WP_IDs':len(ids),'occupied_owner_slots':sum(bool(v) for v in owners),'admission_counter':get(MB+30*4),'queued_MSDUs':get(MB+38*4)}
print(json.dumps({'scope':'Execute linked ni_ingest/ni_select. Same1024 BID queue and124 WP limit; actual MTU and maximum gather vectors, no RPQ completion, RF timing or throughput model.','cases':[{'MSDU_bytes':size,'MSDUs_per_MPDU':gather,'before':replay(sys.argv[1],False,size,gather),'after':replay(sys.argv[2],True,size,gather)} for size,gather in ((1514,5),(64,8))]},indent=2))
