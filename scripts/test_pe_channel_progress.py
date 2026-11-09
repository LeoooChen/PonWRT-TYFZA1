#!/usr/bin/env python3
"""Execute target per-channel scheduling and linked PE blocked-channel progress."""
from pathlib import Path
import argparse,struct,sys
r=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('pe',type=Path);p.add_argument('--wifi',type=Path,required=True);a=p.parse_args()
sys.path.insert(0,str(r/'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4
phl,hci,hal,wd,txbd=0x6000000,0x6010000,0x6020000,0x6100000,0x6200000
credits=[1]*10;submitted=[];queries=[]
def cb(name,v):
 if name=='rtw_hal_get_halcom':return hal
 if name in ('rtw_hal_tx_res_query','rtw_hal_tx_res_query_sw'):
  channel=v[1] if name.endswith('query') else v[2]
  pointers=v[2:4] if name.endswith('query') else [v[3],oracle.uc.reg_read(UC_ARM64_REG_X4)]
  for ptr in pointers:oracle.uc.mem_write(ptr,bytes(2))
  queries.append(channel);return credits[channel]
 if name=='phl_handle_busy_wd':return 0
 if name=='phl_handle_pending_wd':submitted.append(v[3]);assert v[2]==1;return 0
 raise AssertionError((name,v))
oracle=ModuleOracle(a.wifi,['phl_tx_pcie'],cb)
for address,size in ((phl,0x10000),(hci,0x10000),(hal,0x10000),(wd,0x60000),(txbd,0x10000)):oracle.uc.mem_map(address,size)
oracle.word(phl+23840,hci);oracle.word(phl+24032,hal);oracle.word(hci+8,txbd);oracle.word(hci+24,wd)
oracle.uc.mem_write(hci,b'\x0a');oracle.uc.mem_write(hal+748,struct.pack('<I',256))
for channel in range(10):
 ring=wd+channel*0x80a8;oracle.uc.mem_write(ring+96,struct.pack('<H',1));oracle.word(ring+56,ring+128)
for blocked in range(10):
 credits=[1]*10;credits[blocked]=0;queries.clear();submitted.clear();assert oracle.call('phl_tx_pcie',phl)==0
 assert submitted==[i for i in range(10) if i!=blocked]
 assert all(i in queries for i in range(10))
print('Original scheduler: ten DMA-channel zero-credit cases leave other channels runnable')

f=r/'scripts/test_pe_tx_publication.py';e={'__file__':str(f),'__name__':'channel_fixture'}
sys.argv=[str(f),str(a.pe)]
exec(compile(f.read_text().split('for initial_sequence in (0, 15):',1)[0],str(f),'exec'),e)
Machine,TX,MB,BAR,RING,PAGES,PAYLOAD,REGS=(e[n] for n in ('Machine','TX','TXMB','BAR','RING','PAGES','PAYLOAD','REGISTERS'))
def enqueue(m,rings):
 m.put(MB+8*4,len(rings));m.put(MB+9*4,0)
 for slot,ring in enumerate(rings):
  q=[0]*32;q[:4]=[slot,ring,1<<24|80,4];q[8:10]=[PAYLOAD+slot*128,80]
  m.u.mem_write(TX+0x4000+slot*128,struct.pack('<32I',*q))
def owners(m,ring,count):
 result=[]
 for n in range(count):
  page=m.get(RING+(ring*256+n)*8+4);tag=m.get(page+56)&4095
  result.append(m.get(TX+0x40000+(ring*4096+tag)*4)-1)
 return result
cases=0
for blocked in range(6):
 for initial in (0,14,15):
  m=Machine(sequence=initial,paired=False)
  for ring,reg in enumerate(REGS):m.put(MB+(32+ring)*4,0);m.put(BAR+reg,(1 if ring==blocked else 200)<<16)
  # Multiple old blocked entries interleave with two requests per free channel.
  rings=[blocked]+list(range(6))+[blocked]+list(reversed(range(6)))
  enqueue(m,rings);m.run('PE1',42000)
  for ring in range(6):
   expected=[i for i,v in enumerate(rings) if v==ring]
   assert m.get(MB+(32+ring)*4)==(0 if ring==blocked else len(expected)),(blocked,initial,ring)
   if ring!=blocked:assert owners(m,ring,len(expected))==expected
  # Surviving requests have made room in the private circular queue.
  pending=[i for i,v in enumerate(rings) if v==blocked]
  assert (m.get(MB+193*4)-m.get(MB+194*4))&15==len(pending)
  # Restore credit and preserve the blocked channel's original order.
  m.put(BAR+REGS[blocked],200<<16);m.run('PE1',26000)
  assert owners(m,blocked,len(pending))==pending
  assert m.get(MB+193*4)==m.get(MB+194*4)
  cases+=1
print(f'Linked PE scheduler:{cases} blocked-channel, ring wrap, per-channel FIFO and queue compaction cases passed')

# Actual RPQ is the only source of new hardware-tag credit during pause.
# A full tag pool must not prevent that report from being consumed.
for ring in range(6):
 m=Machine(paired=False);report_buffer,forward=0x08000000,0x08100000
 for address in (report_buffer,forward):m.u.mem_map(address,0x10000)
 for i,v in ((68,report_buffer),(72,forward),(32+ring,0)):m.put(MB+i*4,v)
 m.put(BAR+REGS[ring],200<<16)
 tag=12;qsel=ring if ring<4 else 0x12 if ring==4 else 0x11
 if ring:
  m.u.mem_write(TX+0x40000+ring*4096*4,struct.pack('<4096I',*([8]*4096)))
 else:
  m.put(TX+0x34008,4094);m.put(TX+0x3400c,0);m.put(TX+0x40000+tag*4,8)
 enqueue(m,[ring]);m.run('PE1',16000)
 assert not m.get(MB+194*4) and m.get(MB+193*4)==1
 raw=struct.pack('<II',24|0xc000|1<<16,7<<24|4)+bytes(12)+struct.pack('<I',qsel<<8|tag<<16)
 m.u.mem_write(report_buffer,raw);m.put(BAR+0x121c,1<<16);m.put(MB+3*4,2)
 # A full4094-tag scan can already be in flight when ARM posts pause.
 m.run('PE1',90000)
 assert m.get(MB+4*4)==2 and m.get(MB+194*4)==1,(ring,'pause deadlocked on RPQ')
 assert m.get(MB+70*4)==1 and m.get(MB+73*4)==1
 assert m.get(forward+20)>>16&4095==7
 assert owners(m,ring,1)==[0]
 cases+=1

# Revocation is checked even behind an older blocked same-channel request.
for ring in range(6):
 m=Machine(paired=False);m.put(MB+(32+ring)*4,0);m.put(BAR+REGS[ring],1<<16)
 enqueue(m,[ring,ring,(ring+1)%6]);m.put(TX+0x4000+128+28*4,3)
 m.run('PE1',25000)
 assert m.get(TX+0x1e400+4)==1 and not m.get(TX+0x1e400)
 assert m.get(MB+193*4)-m.get(MB+194*4)==1
 cases+=1
print(f'Linked PE scheduler:{cases} total cases including real RPQ-tag release during pause and stale requests behind blocked channels passed')

# Moving a retained NI request must preserve its already assigned sequence
# and GTK generation. Same-channel requests may not overtake a PN stall.
f=r/'scripts/test_pe_bcmc.py';b={'__file__':str(f),'__name__':'channel_bcmc_fixture'}
exec(compile(f.read_text().split("if os.environ.get",1)[0],str(f),'exec'),b)
m=b['machine']();b['choose'](m)
base,mb,req=(b[n] for n in ('base','mb','requests'));key=base+0x1e900+63*40
m.put(key+4,1)
for slot,ring in ((1,0),(2,5)):
 q=[0]*32;q[:4]=[slot,ring,1<<24|80,4];q[8:10]=[b['payload']+1024,80]
 m.u.mem_write(req+slot*128,struct.pack('<32I',*q))
m.put(mb+193*4,3);b['publish'](m)
assert m.get(mb+194*4)==1 and m.get(mb+10*4)==1
assert m.get(base+0x1fd10)==0 and m.get(req+128)==256
assert m.get(req+128+124)==0x80000fff and m.get(req+128+120)==11
assert m.get(req+256)==2 and not m.get(mb+37*4)
m.put(key+4,0);b['publish'](m)
assert m.get(mb+194*4)==3 and m.get(mb+37*4)==2 and m.get(base+0x1fd10)==0
assert m.get(key+20+4)==0 and m.get(key+28+4)==1
# The GTK descriptor was built before the younger raw HIQ descriptor.
wd=b['PAGES']+5*264*128
assert m.get(wd+12)&4095==4095 and m.get(wd+20)==65536
print('NI BCMC PN stall: compacted request keeps sequence/key generation; same HIQ order retained')

# A retained NI tag is released only after real RPQ plus EQ. Service both
# while draining a pause; CPU-only RPQ progress is not enough to prove this.
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP,UC_MIPS_REG_PC
m=b['machine']();b['choose'](m);b['publish'](m)
base,mb,bar,ni=(b[n] for n in ('base','mb','bar','ni'))
wd=b['PAGES']+5*264*128;tag=m.get(wd+56)&4095
m.u.mem_write(base+0x40000+5*4096*4,struct.pack('<4096I',*([8]*4096)))
m.put(base+0x40000+(5*4096+tag)*4,257)
q=[0]*32;q[:4]=[7,5,1<<24|80,4];q[8:10]=[b['payload']+1024,80]
m.u.mem_write(base+0x4000,struct.pack('<32I',*q))
for i,v in ((3,1),(8,1),(9,0),(140,mb|0xa0000000),(142,1),(193,0),(194,0)):m.put(mb+i*4,v)
m.u.mem_map(0x10020000,0x1000);m.u.reg_write(UC_MIPS_REG_A0,mb|0xa0000000);m.u.reg_write(UC_MIPS_REG_SP,0xa7e01ff0)
m.u.emu_start(b['e']['symbols']['pe_main'],0x9fc0fff0,count=90000)
assert m.get(mb+193*4)==1 and not m.get(mb+194*4)
raw=struct.pack('<II',24|0xc000|1<<16,7<<24|4)+bytes(12)+struct.pack('<I',0x11<<8|tag<<16)
m.u.mem_write(b['REPORTS'],raw);m.put(bar+0x121c,1<<16);m.put(mb+3*4,2)
m.put(ni+0x6340,0)
m.u.emu_start(m.u.reg_read(UC_MIPS_REG_PC),0x9fc0fff0,count=90000)
assert m.get(mb+70*4)==1 and m.get(base+0x20004)&1<<31
assert m.get(mb+4*4)==1 and not m.get(mb+194*4), 'EQ still owns the tag'
m.put(ni+0x6340,(1<<31)|1);m.put(ni+0x6380,1<<31)
m.u.emu_start(m.u.reg_read(UC_MIPS_REG_PC),0x9fc0fff0,count=90000)
assert m.get(mb+4*4)==2 and m.get(mb+194*4)==1 and not m.get(base+0x20000)
assert m.get(ni+0x6384)==b['payload']|6 and not m.get(mb+30*4)
print('Pause with NI tag exhaustion: actual RPQ then EQ credit release completes the pending CPU request')
