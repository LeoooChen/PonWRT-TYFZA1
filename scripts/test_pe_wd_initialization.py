#!/usr/bin/env python3
"""Compare published WD/payload bytes and builder work against the accepted PE."""
from pathlib import Path
import sys,struct,json
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/test_pe_tx_publication.py').read_text().split('for initial_sequence in (0, 15):')[0]
directories=sys.argv[1:3]
states=[]
for directory in directories:
 sys.argv=['compare',directory]
 ns={'__file__':str(root/'scripts/test_pe_tx_publication.py')}
 exec(compile(source,ns['__file__'],'exec'),ns)
 states.append(ns)
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_RA

def case(ns,kind,count,seed):
 m=ns['Machine'](paired=False)
 TX,MB,PAYLOAD,PAGES,RING=[ns[x] for x in ('TX','TXMB','PAYLOAD','PAGES','RING')]
 m.u.mem_write(TX+0x1e000,bytes([seed])*0x2000)
 m.put(MB+24*4,1);m.put(MB+14*4,PAGES+0x30000)
 req=[0]*32
 req[0]=256 if kind=='ni' else 1
 req[1]=0
 flags=(1<<24)|(1<<17 if seed&1 else 0)|(1<<16 if seed&2 else 0)
 if kind in ('ni','encap'):flags|=1<<18
 if kind=='raw':flags|=1<<22
 if kind=='ni' and count>1:flags|=1<<21
 req[4]=flags;req[5]=64|2<<16
 req[3]=0x180|3<<16|1<<20|1<<21
 req[6]=0x12345678;req[7]=0x789a
 payload_length=0
 for i in range(count):
  length=100+(seed+i)%64
  address=PAYLOAD+i*2048+80
  frame=bytes.fromhex('0200000000020200000000030800')+bytes([i+1])*(length-14)
  m.u.mem_write(address,frame)
  req[8+i*2]=address;req[9+i*2]=length
  payload_length+=length
 header=26 if kind!='plain' else 0
 if kind=='ni':
  size=26+sum(req[9+i*2]+8 for i in range(count))
  size+=sum((-req[9+i*2]-8)&3 for i in range(count-1))
  if count==1:size=26+8+req[9]-14
 elif kind=='encap':size=payload_length+20
 else:size=payload_length
 req[2]=count<<24|header<<16|size
 m.u.mem_write(PAGES+0x30000+128,bytes.fromhex('88020000')+bytes(60))
 m.u.mem_write(TX+0x4000+254*128,struct.pack('<32I',*req));m.put(MB+8*4,255)
 stat={'instructions':0,'stores':0};active=[False,0]
 def observe(uc,at,size,data):
  if at==ns['SYMBOLS']['pe_build_tx']:active[:]=[True,uc.reg_read(UC_MIPS_REG_RA)]
  if active[0] and at==active[1]:active[0]=False
  if active[0]:
   stat['instructions']+=1
   ins=m.get(at&0x1fffffff)
   stat['stores']+=ins>>26 in (0x28,0x29,0x2a,0x2b,0x2e)
 m.u.hook_add(UC_HOOK_CODE,observe)
 m.run('PE1',8000)
 assert m.get(MB+10*4)==1,(kind,count)
 used=m.get(RING)&0xffff
 image=bytes(m.u.mem_read(m.get(RING+4),(used+3)&~3))
 payload=bytes(m.u.mem_read(PAYLOAD,0x5000))
 return image,payload,stat
rows=[]
for kind,max_count in [('plain',10),('raw',9),('encap',9),('ni',8)]:
 for count in range(1,max_count+1):
  for seed in (0x55,0xaa,0xff):
   a,b=[case(ns,kind,count,seed) for ns in states]
   assert a[:2]==b[:2],(kind,count,seed)
   rows.append(dict(kind=kind,count=count,seed=seed,before=a[2],after=b[2]))
print(json.dumps({'cases':len(rows),'published_bytes_and_payloads_identical':True,'seven_MSDU':[r for r in rows if r['kind']=='ni' and r['count']==7]},indent=2))
