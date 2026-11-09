#!/usr/bin/env python3
"""Run actual linked CPU/NI builders with distinct role identity and addresses."""
import json,struct,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
from test_pe_downlink_gather import case
cases=0
for role in (0,1,7,0x10,0x40):
 for client in (False,True):
  for lengths in ([60],[1514]*7):
   for multicast in (False,True):
    case(lengths,10896,from_epp=True,client=client,multicast=multicast,role=role,self_address='020000aabbcc')
    cases+=1
# A selected NI request must retain its old self-address when later template
# state changes before final publication (model the boundary, not a real race).
import subprocess
from unicorn import UC_HOOK_CODE
symbols={line.split()[2]:int(line.split()[0],16) for line in subprocess.check_output(['llvm-nm-18',str(Path(sys.argv[1])/'pe-wd.elf')],text=True).splitlines() if len(line.split())==3}
changed=[False]
def boundary(u):
 def step(cpu,address,size,data):
  if address!=symbols['pe_tx_publish'] or changed[0]:return
  rd=int.from_bytes(cpu.mem_read(0x4002000+194*4,4),'little');wr=int.from_bytes(cpu.mem_read(0x4002000+193*4,4),'little')
  if rd==wr:return
  cpu.mem_write(0x4000000+0x68408,bytes.fromhex('020000dddddd'));changed[0]=True
 u.hook_add(UC_HOOK_CODE,step)
case([60],10896,from_epp=True,role=5,self_address='020000aabbcc',observe=boundary)
assert changed[0];cases+=1
# Real CPU request intake and private FIFO. Distinct WP addresses survive later
# global/peer template changes while hardware credit prevents publication.
f=root/'scripts/test_pe_tx_publication.py';ns={'__file__':str(f)}
exec(compile(f.read_text().split('for initial_sequence in (0, 15):')[0],str(f),'exec'),ns)
Machine=ns['Machine'];TX,MB,PAYLOAD,PAGES,RING,BAR=[ns[n] for n in ('TX','TXMB','PAYLOAD','PAGES','RING','BAR')]
for client in (False,True):
 for role in (0,5,0x20,0x47):
  m=Machine(paired=False);headers=PAGES+0x30000;m.put(MB+14*4,headers)
  m.u.mem_write(MB+64,bytes.fromhex('020000001111'));m.u.mem_write(TX+0xe408,bytes.fromhex('020000002222'))
  ethernet=bytes.fromhex('0200000033330200000044440800')+bytes(range(50))
  m.u.mem_write(PAYLOAD,ethernet);m.u.mem_write(headers+128,ethernet[:14]);self=bytes.fromhex('020000aabbcc');m.u.mem_write(headers+128+96,self+bytes(2))
  req=[0]*32;req[0]=1;req[2]=1<<24|26<<16|84;req[3]=4|role<<24|1<<23;req[4]=1<<24|1<<18|(1<<23 if client else 0);req[8]=PAYLOAD;req[9]=64
  m.u.mem_write(TX+0x4000+254*128,struct.pack('<32I',*req));m.put(MB+8*4,255)
  # Hardware producer begins at NEXT0==0. Setting consumer1 blocks ring0.
  m.put(BAR+0x1058,1<<16);m.run('PE1',15000);assert not m.get(MB+10*4)
  m.u.mem_write(MB+64,bytes.fromhex('020000009999'));m.u.mem_write(TX+0x68408,bytes.fromhex('020000008888'))
  m.put(BAR+0x1058,0);m.run('PE1',20000);assert m.get(MB+10*4)==1
  page=m.get(RING+4);assert m.get(page+32)&127==role
  emitted=bytes(m.u.mem_read(headers+128,34));assert emitted[10:16]==(ethernet[6:12] if client else self)
  assert emitted[4:10]==(bytes.fromhex('020000002222') if client else ethernet[:6])
  assert emitted[16:22]==(ethernet[:6] if client else ethernet[6:12])
  cases+=1
print(f'Linked PE TX identity: {cases} CPU/NI/AP/STA/multicast/A-MSDU and credit-stall snapshots passed')

from check_pe_uplink_small_udp import case as receive
rx_cases=0
for client in (False,True):
 for slot in range(2):
  for amsdu in (False,True):
   for corrupt in (None,'receiver','transmitter','admission'):
    result=receive(sys.argv[1],slot,False,100,client=client,amsdu=amsdu,corrupt=corrupt,peer_self=True)
    assert result['PE_to_NI']==(corrupt is None);rx_cases+=1
print(f'Linked PE RX identity: {rx_cases} per-peer address and foreign-receiver/admission cases passed')
