#!/usr/bin/env python3
"""Execute NI ingress and TX selection for identical peer MACs on different BSSes."""
from pathlib import Path
import struct
import sys
root=Path(__file__).resolve().parents[1]
f=root/'scripts/test_pe_ps_selection.py';ns={'__file__':str(f)}
exec(compile(f.read_text().split('\nrows = []')[0],str(f),'exec'),ns)
Radio=ns['ns']['Radio'];call=ns['call'];BASE,MB=ns['BASE'],ns['MB']
PAYLOAD,EPP,NI=ns['ns']['PAYLOAD'],ns['ns']['EPP'],ns['ns']['NI']
checks=0
for client in (False,True):
 for ids in ([1,17,1,17],[17,17,1,1],[0,61,17,1]):
  m=Radio(peers=2);m.packets([1]*len(ids))
  for peer,wifi in ((1,1),(2,17)):
   m.put(BASE+0x11a00+peer*4,wifi);m.put(BASE+0xe800+peer*16,0)
   m.u.mem_write(BASE+0xe400+peer*8,bytes([2,0,0,0,0,1]))
   m.u.mem_write(BASE+0x68400+peer*8,bytes([2,0,0,0,0,10+peer])+bytes(2))
   m.put(BASE+0xd000+peer*32+12,m.get(BASE+0xd000+peer*32+12)|(1<<23)|((peer-1)<<24))
   if client:m.put(BASE+0xd000+peer*32+16,m.get(BASE+0xd000+peer*32+16)|(1<<23))
  for i,wifi in enumerate(ids):
   m.put(EPP+i*8+4,(wifi<<14)|1516)
   if not wifi: # ordinary HeaderA, excluded from VIF routing
    m.u.mem_write(PAYLOAD+i*2048+64,struct.pack('>II',1<<31,1508<<15))
  call(m,'ni_ingest');assert m.get(NI+0x7160)==len(ids)*8
  assert m.get(BASE+0x11804)==ids.count(1) and m.get(BASE+0x11808)==ids.count(17)
  for peer,wifi in ((1,1),(2,17)):
   address=m.get(BASE+0x15000+peer*8*4);seen=[]
   while address:
    seen.append((address-PAYLOAD)//2048);address=m.get(address)
   assert seen==[i for i,x in enumerate(ids) if x==wifi],(ids,peer,seen)
  # Existing linked selector must keep physical peer/key/role after ingress.
  m.clock(101)
  for _ in range(12):
   if not m.get(MB+38*4):break
   selected=call(m,'ni_select')
   if selected:
    request=BASE+0x7000;peer=m.get(request+16)>>24
    assert peer in (1,2) and ((m.get(request+12)>>24)&127)==peer-1
  for slot in range(8):
   address=m.get(BASE+0x20000+slot*8)
   if not address:continue
   macid=(m.get(BASE+0x20004+slot*8)>>8)&127
   while address:
    idx=(address-PAYLOAD)//2048
    assert macid==({1:1,17:2}.get(ids[idx],0)),(ids,idx,macid)
    address=m.get(address)
  checks+=1
# Revocation must be reread even for repeated DA within the same batch.
from unicorn import UC_HOOK_MEM_READ
m=Radio(peers=2);m.packets([1,1]);m.put(BASE+0x11a08,17)
m.u.mem_write(BASE+0xe410,bytes([2,0,0,0,0,1]))
reads=[0]
def close(cpu,access,address,size,value,data):
 reads[0]+=1
 if reads[0]==2:m.put(BASE+0x11a04,0)
m.u.hook_add(UC_HOOK_MEM_READ,close,begin=BASE+0x11a04,end=BASE+0x11a07)
call(m,'ni_ingest');assert m.get(BASE+0x11804)==1 and not m.get(BASE+0x11808)
print(f'Linked downlink identity: {checks} same-DA/AP/STA/mixed-ID batches, exact BID/peer owners, zero/foreign ID and cached revocation PASS')

# Invalid/no-owner IDs return every BID without publishing any radio TXBD.
for wifi in (0,2,60,61,63):
 m=Radio(peers=1);m.packets([1,1]);m.put(BASE+0xe810,0)
 for i in range(2):
  m.put(EPP+i*8+4,(wifi<<14)|1516)
  if not wifi:m.u.mem_write(PAYLOAD+i*2048+64,struct.pack('>II',1<<31,1508<<15))
 m.run(100000);m.check_returns()
 assert m.returns==[PAYLOAD|6,(PAYLOAD+2048)|6] and not m.tx_consumers
print('Zero, unowned and reserved EPP IDs: all BIDs recycled exactly once without TX publication PASS')
