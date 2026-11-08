#!/usr/bin/env python3
"""Execute CPU multicast stamp revocation through actual PE staging/publication."""
from pathlib import Path
import struct
root=Path(__file__).resolve().parents[1]
f=root/'scripts/test_fttr_pe_generation.py';e={'__file__':str(f),'__name__':'cpu_mcast_fixture'}
exec(compile(f.read_text().split('cases = 0',1)[0],str(f),'exec'),e)
OriginalMachine,TX,MB,BAR,PAYLOAD,DISCARD,TABLE,request=(e[n] for n in ('Machine','TX','MB','BAR','PAYLOAD','DISCARD','TABLE','request'))
def Machine(**kwargs):
 m=OriginalMachine(**kwargs)
 m.put(MB+8*4,0);m.put(MB+9*4,0)
 for i,reg in enumerate(e['REGISTERS']):m.put(MB+(32+i)*4,0);m.put(BAR+reg,0)
 return m
STAMPS=TX+0x68000
cases=0
for ni in (0,1):
 for ring in range(6):
  for stamp,current in ((1,2),(0xfffffffe,0xffffffff),(0xffffffff,0),(17,0)):
   for segments in (1,10):
    m=Machine(paired=False);m.put(MB+24*4,ni);m.put(MB+210*4,TABLE)
    m.put(TABLE+12,current);request(m,0,7,ring,0,segments)
    m.put(STAMPS+7*4,stamp);m.put(MB+8*4,1)
    m.run('PE1',15000)
    assert m.get(DISCARD+7*4)==1 and not m.get(MB+10*4),(ni,ring,stamp,current,segments)
    assert m.get(MB+9*4)==1 and m.get(MB+193*4)==m.get(MB+194*4)
    # The stamp shares no address words and software cancellation takes no tag.
    assert not any(m.get(TX+0x40000+(ring*4096+i)*4) for i in range(2))
    m.put(DISCARD+7*4,0);m.run('PE1',15000);assert not m.get(DISCARD+7*4)
    cases+=1
# After selection at zero hardware credit, a later generation invalidates
# the private FIFO entry even if unrelated ARM ring slots have wrapped.
for ring in range(6):
 m=Machine(paired=False);m.put(MB+210*4,TABLE);m.put(TABLE+12,3)
 m.put(STAMPS+9*4,3);request(m,0,9,ring,0)
 index=(0x1058,0x105c,0x1060,0x1064,0x1078,0x107c)[ring]
 m.put(BAR+index,1<<16);m.put(MB+8*4,1);m.run('PE1',15000)
 assert not m.get(MB+10*4) and not m.get(DISCARD+9*4)
 assert m.get(MB+193*4)!=m.get(MB+194*4)
 m.put(TABLE+12,4);m.run('PE1',15000)
 assert m.get(DISCARD+9*4)==1 and not m.get(MB+10*4)
 # Actual reuse starts with a fresh zero side stamp and must not inherit old rejection.
 m.put(DISCARD+9*4,0);m.put(STAMPS+9*4,0);request(m,1,9,ring,0)
 m.put(BAR+index,0);m.put(MB+8*4,2);m.run('PE1',15000)
 assert m.get(MB+10*4)==1 and not m.get(DISCARD+9*4)
 cases+=1
# Valid stamped and ordinary packets coexist; unpublished stale entries do
# not consume channel position or stop unrelated requests behind them.
m=Machine(paired=False);m.put(MB+210*4,TABLE);m.put(TABLE+12,5)
for slot,(ident,stamp) in enumerate(((1,4),(2,5),(3,0))):
 request(m,slot,ident,0,0);m.put(STAMPS+ident*4,stamp)
m.put(MB+8*4,3);m.run('PE1',30000)
assert m.get(DISCARD+4)==1 and not m.get(DISCARD+8) and not m.get(DISCARD+12)
assert m.get(MB+10*4)==2 and m.get(MB+32*4)==2
# A generation change after publication cannot pretend the WP completed.
m.put(TABLE+12,6);m.run('PE1',15000)
assert not m.get(DISCARD+8) and m.get(TX+0x40000)==3
cases+=1
print(f'CPU multicast PE: {cases} revocation, 10-address requests, credit stalls, reuse, mixed FIFO and already-published cases passed')
