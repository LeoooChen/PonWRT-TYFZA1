#!/usr/bin/env python3
"""Execute independent multicast BID and EPP-prefix retirement on linked PE."""
from pathlib import Path
import struct
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP
root=Path(__file__).resolve().parents[1];f=root/'scripts/test_fttr_ni_pe_drain.py'
e={'__file__':str(f),'__name__':'mcast_drain_fixture'}
exec(compile(f.read_text().split('cases = 0',1)[0],str(f),'exec'),e)
machine,base,mb,ni,bar,epp,payload,table,requests=(e[n] for n in ('machine','base','mb','ni','bar','epp','payload','table','requests'))
DRAIN=base+0x1e840;FTTR=base+0x1e800

def drain(m,sequence=1):
 m.put(DRAIN,sequence);m.call('ni_prefix_drain',mb,base,DRAIN)

def recycle(m,count):
 m.put(ni+0x6340,(1<<31)|count);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
 m.call('ni_prefix_drain',mb,base,DRAIN)

cases=0
for source in (0,4,6,7,18,63):
 for cos in range(8):
  for full in (False,True):
   m=machine(source,cos,full);m.ingest()
   assert m.get(DRAIN+8)==1 and m.get(FTTR+8)==(source==6)
   m.put(table,0);drain(m)
   m.put(base+0xe810,1);assert not m.call('ni_select',mb,base,requests)
   recycle(m,0);assert not m.get(DRAIN+4) and m.get(DRAIN+8)==1
   m.put(ni+0x6340,16383);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
   assert m.get(DRAIN+8)==1 and not m.get(DRAIN+4)
   recycle(m,1);assert m.get(DRAIN+4)==1 and not m.get(DRAIN+8) and not m.get(FTTR+8)
   cases+=1
# No-cookie ordinary traffic is outside this independent multicast counter.
for source in (0,6,7):
 m=machine(source=source,cookie=False);m.ingest();drain(m)
 assert not m.get(DRAIN+8) and m.get(DRAIN+4)==1 and m.get(FTTR+8)==(source==6);cases+=1
# Snapshot all8 COS queues after close; malformed head keeps the prefix owned.
for cos in range(8):
 for initial in (0,4088):
  m=machine(source=0,cos=cos);m.put(base+0x11c00+cos*4,initial)
  m.u.mem_write(epp+cos*4096+initial,struct.pack('<II',payload,0))
  m.put(ni+0x7060+cos*4,(initial+8)&4095);m.put(table,0);drain(m)
  assert m.get(DRAIN+16)==1<<cos and not m.get(DRAIN+4)
  m.call('ni_ingest',mb,base);assert m.get(DRAIN+16)==1<<cos
  m.u.mem_write(epp+cos*4096+initial,struct.pack('<II',payload|0x50,0))
  m.call('ni_ingest',mb,base);assert not m.get(DRAIN+16) and m.get(DRAIN+8)==1
  m.call('ni_select',mb,base,requests);recycle(m,1);assert m.get(DRAIN+4)==1;cases+=1
# Independent FTTR and multicast snapshots can require different COS prefixes.
m=machine(source=0);m.put(table,0)
for cos in range(8):
 m.u.mem_write(payload+cos*256,bytes(m.u.mem_read(payload,256)))
 m.u.mem_write(epp+cos*4096,struct.pack('<II',(payload+cos*256)|0x50,0));m.put(ni+0x7060+cos*4,8)
m.put(ni+0x659c,255<<24);drain(m)
m.put(FTTR,3);m.put(FTTR+12,3);m.put(FTTR+16,1<<7);m.put(FTTR+20+7*4,8)
for cos in range(8):
 m.call('ni_ingest',mb,base)
 assert not m.get(DRAIN+16)&((1<<(cos+1))-1)
assert not m.get(DRAIN+16) and not m.get(FTTR+16) and m.get(DRAIN+8)==8
for n in range(8):m.call('ni_select',mb,base,requests);recycle(m,1)
assert m.get(DRAIN+4)==1 and not m.get(DRAIN+8);cases+=1
# Invalid admission/excess sleeping-pool discard still owns the original cookie.
for overflow in (False,True):
 m=machine(source=0)
 if overflow:m.put(base+0xe810,1);m.put(mb+38*4,2048)
 else:m.put(table,0)
 m.call('ni_ingest',mb,base)
 assert m.get(DRAIN+8)==1 and m.get(payload+16)
 if not overflow:m.call('ni_select',mb,base,requests)
 m.put(table,0);drain(m);recycle(m,1)
 assert not m.get(DRAIN+8) and m.get(DRAIN+4)==1;cases+=1
# Selected or hardware-published copies retain BID ownership through actual RPQ.
for published in (False,True):
 m=machine(source=0);m.ingest();m.put(bar+0xc438,10000)
 assert m.call('ni_select',mb,base,requests)==1;m.put(mb+193*4,1)
 if published:m.call('pe_tx_publish',mb,8,requests)
 m.put(table,0);drain(m)
 if not published:
  m.put(bar+0x1058,1<<16);m.call('pe_tx_publish',mb,8,requests)
 else:
  recycle(m,0);assert m.get(DRAIN+8)==1 and not m.get(DRAIN+4)
  raw=struct.pack('<II',24|0xc000|1<<16,7<<24|4)+bytes(12)+struct.pack('<I',1|(m.get(e['PAGES']+56)&4095)<<16)
  m.u.mem_write(e['REPORTS'],raw);m.put(bar+0x121c,1<<16)
  m.call('pe_rpq',mb,base)
 recycle(m,0);assert m.get(DRAIN+8)==1 and not m.get(DRAIN+4)
 recycle(m,1);assert m.get(DRAIN+4)==1 and not m.get(DRAIN+8);cases+=1
# A two-BID aggregate reports completion only after the final EQ push.
m=machine(source=0);m.put(base+0xe204,7935<<18)
m.u.mem_write(payload+2048,bytes(m.u.mem_read(payload,2048)));m.u.mem_write(epp+8,struct.pack('<II',(payload+2048)|0x50,0));m.put(ni+0x7060,16)
m.call('ni_ingest',mb,base);m.put(bar+0xc438,10000)
assert m.call('ni_select',mb,base,requests)==2;m.put(mb+193*4,1);m.put(table,0);drain(m)
m.call('pe_tx_publish',mb,8,requests);recycle(m,1)
assert m.get(DRAIN+8)==1 and not m.get(DRAIN+4);recycle(m,1)
assert not m.get(DRAIN+8) and m.get(DRAIN+4)==1;cases+=1
# New request invalidates old ACK; disabled NI has no EPP geometry to sample.
m=machine(source=0);m.put(ni+0x7060,0);m.put(table,0);drain(m)
assert m.get(DRAIN+4)==1
m.put(ni+0x7060,8);m.ingest();assert m.get(DRAIN+8)==1
drain(m,2);assert m.get(DRAIN+4)==1
m.call('ni_select',mb,base,requests);recycle(m,1);assert m.get(DRAIN+4)==2
m.put(mb+24*4,0);m.put(mb+114*4,0xffffffff);drain(m,3);assert m.get(DRAIN+4)==3;cases+=1
# The normal PE1 loop polls both drain structures while closed/no-NI. It
# must not borrow the FTTR ACK for a multicast request or vice versa.
m=machine(source=0);m.put(ni+0x7060,0);m.put(mb+24*4,0);m.put(table,0)
m.put(FTTR,41);m.put(DRAIN,73);m.u.mem_map(0x10020000,0x1000)
for i,v in ((3,1),(140,mb|0xa0000000),(142,1)):m.put(mb+i*4,v)
m.u.reg_write(UC_MIPS_REG_A0,mb|0xa0000000);m.u.reg_write(UC_MIPS_REG_SP,0xa7e01ff0)
m.u.emu_start(e['env']['symbols']['pe_main'],0x9fc0fff0,count=20000)
assert m.get(FTTR+4)==41 and m.get(DRAIN+4)==73
cases+=1
# Direct AP BCMC also carries a cookie, but no STA/MACID PS counter.
f=root/'scripts/test_pe_bcmc.py';b={'__file__':str(f),'__name__':'bcmc_drain_fixture'}
exec(compile(f.read_text().split("if os.environ.get('RTL_TEST_AP_GROUP_RECORD'):",1)[0],str(f),'exec'),b)
for published in (False,True):
 m=b['machine']();m.put(mb+24*4,1);b['choose'](m)
 if published:b['publish'](m)
 m.put(table,0);drain(m)
 assert m.get(DRAIN+8)==1 and not m.get(DRAIN+4)
 if published:b['finish'](m)
 else:b['publish'](m);recycle(m,1)
 m.call('ni_prefix_drain',mb,base,DRAIN)
 assert not m.get(DRAIN+8) and m.get(DRAIN+4)==1
 cases+=1
print(f'Multicast NI/PE drain:{cases} source/COS/header, prefix wrap, invalid admission, PS overflow, RPQ, partial EQ and retry cases passed')
