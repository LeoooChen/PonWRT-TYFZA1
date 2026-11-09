#!/usr/bin/env python3
"""Execute direct AP BCMC through real linked EPP/HIQ/PN/sequence/RPQ/EQ code."""
from pathlib import Path
import struct
import os
root=Path(__file__).resolve().parents[1]
f=root/'scripts/test_pe_mcast.py';e={'__file__':str(f),'__name__':'bcmc_fixture'}
exec(compile(f.read_text().split('vectors=0',1)[0],str(f),'exec'),e)
Machine,base,mb,ni,bar,payload,table,requests=(e[n] for n in ('Machine','base','mb','ni','bar','payload','table','requests'))
RING,PAGES,REPORTS,FWD=0x5000000,0x5100000,0x5200000,0x5300000
SEQ,BCMC=base+0x1fd00,base+0x1fd40

def machine(cam=63,keyid=2,rate=4,group=None,length=84,full=True,gcmp=False,extended=False,role=0,macid=0):
 m=Machine(full=full)
 m.frame=m.frame[:14]+bytes([0xa5])*(length-14)
 m.u.mem_write(payload+64+m.header,bytes(m.frame))
 m.u.mem_write(payload+64,struct.pack('>II',(1<<31 if full else 0)|265<<18,(length+m.header-8)<<15|19<<3))
 for a,n in ((RING,0x4000),(PAGES,0x32000),(REPORTS,0x10000),(FWD,0x10000)):m.u.mem_map(a,n)
 for i,v in ((11,RING),(12,PAGES),(14,base+0x4000),(24,1),(116,0x6380),(117,6),(68,REPORTS),(72,FWD)):
  m.put(mb+i*4,v)
 ap=bytes.fromhex('020000000001');group=group or e['group']
 m.u.mem_write(mb+64,ap);m.u.mem_write(payload+64+m.header,group)
 key=(cam<<16|keyid<<24|(1<<27 if gcmp else 0)|(1<<28 if extended else 0)) if cam is not None else 0
 generation=11 if cam is not None else 0
 domain=role if role<8 else 7+(role>>4)
 m.seq=base+(0x1fd00 if not domain else 0x68c00+(domain-1)*20)
 m.bcmc=base+(0x1fd40 if not domain else 0x69000+(domain-1)*28)
 m.token=23|(domain<<27);m.role=role;m.macid=macid
 m.put(m.seq,23);m.put(m.seq+16,4095)
 m.u.mem_write(m.bcmc,struct.pack('<5I',29,m.token,generation,rate|role<<24,key)+ap+bytes([macid,0]))
 record=struct.pack('<II',7,1<<15)+group+bytes(2)+struct.pack('<4I',29,m.token,generation,rate|role<<24)+bytes(48)+ap+bytes(2)
 record+=struct.pack('<II',key,(macid<<24)|(1<<18)|(1<<13)|(1<<14)|(1<<15 if generation else 0))
 assert len(record)==96
 m.u.mem_write(table+16+9*96,record)
 if cam is not None:
  pn=base+0x1e900+cam*40;m.put(pn,11);m.put(pn+20,0xffffffff)
 return m

def choose(m):
 m.ingest();assert m.get(base+0x15000)==payload
 assert m.call('ni_select',mb,base,requests)==1
 assert m.get(requests+4)==5 and m.get(requests+8)==(1<<24|24<<16|(len(m.frame)+18))
 assert m.get(requests+16)>>24==m.macid and m.get(requests+124)==m.token
 m.put(mb+193*4,1)

def publish(m):m.call('pe_tx_publish',mb,8,requests)

def finish(m):
 from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP
 wd=PAGES+5*264*128;tag=m.get(wd+56)&4095
 report=0x11<<8|tag<<16|m.macid
 raw=struct.pack('<II',24|0xc000|1<<16,7<<24|4)+bytes(12)+struct.pack('<I',report)
 m.u.mem_write(REPORTS,raw);m.put(bar+0x121c,1<<16)
 m.u.mem_map(0x10020000,0x1000)
 for i,v in ((3,1),(140,mb|0xa0000000),(142,1)):m.put(mb+i*4,v)
 m.u.reg_write(UC_MIPS_REG_A0,mb|0xa0000000);m.u.reg_write(UC_MIPS_REG_SP,0xa7e01ff0)
 m.u.emu_start(e['symbols']['pe_main'],0x9fc0fff0,count=20000)
 assert m.get(base+0x20004)&1<<31 and m.get(base+0x20000)==payload
 m.put(ni+0x6340,(1<<31)|1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
 assert not m.get(base+0x20000) and not m.get(mb+30*4)
 assert not m.get(base+0x40000+(5*4096+tag)*4)

if os.environ.get('RTL_TEST_AP_GROUP_RECORD'):
 m=machine(gcmp=True,extended=True);record=Path(os.environ['RTL_TEST_AP_GROUP_RECORD']).read_bytes();assert len(record)==96
 epoch,control=struct.unpack_from('<II',record);m.put(table,epoch)
 m.u.mem_write(table+16+9*96,record);m.u.mem_write(payload+64+m.header,record[8:14]);m.u.mem_write(mb+64,record[80:86]);m.u.mem_write(BCMC+20,record[80:86])
 # Feed the exact NI source and packet size captured by the complete learner.
 m.u.mem_write(payload+68,struct.pack('>I',(len(m.frame)+8)<<15|((control>>16)&63)<<9|19<<3))
 choose(m);publish(m);assert m.get(mb+10*4)==1;finish(m)
 print('Complete bridge/FE record reached direct BCMC HIQ and real RPQ/EQ')

cases=0
for extended in (False,True):
 for gcmp in (False,True):
  for cam in (None,0,63,127):
   for keyid in range(4):
    for group in (e['group'],bytes.fromhex('333300010203'),bytes([255])*6):
     m=machine(cam,keyid,group=group,gcmp=gcmp,extended=extended);choose(m);publish(m)
     assert m.get(mb+194*4)==1 and m.get(SEQ+16)==0
     wd=PAGES+5*264*128
     assert (m.get(wd)>>16)&15==9 and (m.get(wd+8)>>17)&63==0x11
     assert not m.get(wd)&1<<6 and not m.get(wd+12)&1<<12
     assert m.get(wd+12)&4095==4095 and m.get(wd+8)>>24==0
     length,address=struct.unpack('<HI',m.u.mem_read(wd+64,6))
     frame=bytes(m.u.mem_read(address,length&2047))
     expected=bytes.fromhex('08420000' if cam is not None else '08020000')+group+bytes.fromhex('020000000001')+bytes(m.frame[6:12])+bytes.fromhex('f0ffaaaa030000000800')+bytes(m.frame[14:])
     assert frame==expected
     if cam is not None:
      assert m.get(wd+4)&15==((8 if gcmp else 6)+extended)
      assert m.get(wd+4)&64 and m.get(wd+20)==65536
      assert (m.get(wd+40)&511)==256|cam and m.get(wd+4)&0x30==keyid<<4
     finish(m);cases+=1
for stage in ('ingress','queued','selected'):
 for fault in range(8):
  m=machine()
  if stage!='ingress':m.ingest()
  if stage=='selected':
   assert m.call('ni_select',mb,base,requests)==1;m.put(mb+193*4,1)
  if fault==0:m.put(table,0)
  if fault==1:m.put(BCMC,0)
  if fault==2:m.put(BCMC,30)
  if fault==3:m.put(SEQ,24)
  if fault==4:m.put(base+0x1e900+63*40,12)
  if fault==5:m.put(BCMC+12,7)
  if fault==6:m.u.mem_write(BCMC+20,bytes.fromhex('020000000003'))
  if fault==7:m.put(BCMC+16,m.get(BCMC+16)^(1<<27))
  if stage=='ingress':m.ingest()
  if stage=='selected':publish(m)
  else:assert not m.call('ni_select',mb,base,requests)
  assert not m.get(mb+10*4) and m.get(SEQ+16)==4095
  m.put(ni+0x6340,(1<<31)|1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
  assert not m.get(mb+30*4);cases+=1
# All eight priorities select the same non-QoS HIQ recipient; the record
# retains actual Linux policy instead of leaking an incoming CoS into QSEL.
for cos in range(8):
 m=machine();m.u.mem_write(payload+68,struct.pack('>I',(len(m.frame)+8)<<15|19<<3|cos))
 choose(m);publish(m);assert m.get(requests+4)==5 and not m.get(requests+16)&7;finish(m);cases+=1
# BCMC uses HIQ/DTIM, never ordinary per-STA PS grants/EOSP.
m=machine();m.put(base+0xe800,1);m.put(base+0xe804,91);m.put(base+0xe80c,7)
choose(m);publish(m)
assert not m.get(requests+16)&((1<<19)|(1<<20)) and not m.get(base+0x20004)&(1<<16)
finish(m);cases+=1
# Foreign source/destination, flag combinations, DA and mode cannot enter HIQ.
for fault in range(8):
 m=machine();record=table+16+9*96
 if fault==0:m.put(record+4,1<<15|1<<16)
 if fault==1:m.put(record+4,1<<15|1<<8)
 if fault==2:m.put(record+92,m.get(record+92)|1<<16)
 if fault==3:m.u.mem_write(payload+80,bytes.fromhex('01005e010204'))
 if fault==4:m.u.mem_write(record+86,b'\1')
 if fault==5:m.u.mem_write(record+15,b'\1')
 if fault==6:m.u.mem_write(payload+68,struct.pack('>I',(len(m.frame)+8)<<15|18<<3))
 if fault==7:m.put(record+16,30)
 m.ingest();assert not m.call('ni_select',mb,base,requests) and not m.get(mb+10*4);cases+=1
# Long BIDs and compact HEADER_A preserve headroom/generation metadata.
for full in (False,True):
 for size in (14,60,1514,1600-(16 if full else 8)):
  m=machine(length=size,full=full);choose(m);publish(m)
  wd=PAGES+5*264*128;length,address=struct.unpack('<HI',m.u.mem_read(wd+64,6))
  assert length&2047==size+18 and address>=payload+32 and address+(length&2047)<=payload+2048
  assert m.get(payload+28)==23
  finish(m);cases+=1
# One byte beyond the exact original QM tailroom contract is rejected.
for full in (False,True):
 m=machine(length=1601-(16 if full else 8),full=full);m.ingest()
 assert m.get(payload+16)==256 and not m.call('ni_select',mb,base,requests)
 assert not m.get(mb+10*4);cases+=1
# Refused ingress cannot be revived by a later template/epoch reopen.
m=machine();m.put(BCMC,0);m.ingest();m.put(BCMC,29)
assert m.get(payload+16)==256 and not m.call('ni_select',mb,base,requests)
assert not m.get(mb+10*4);cases+=1
# PN contention preserves one allocated sequence; closure consumes neither PN nor TXBD.
m=machine();choose(m);key=base+0x1e900+63*40;m.put(key+4,1);publish(m)
assert m.get(SEQ+16)==0 and m.get(requests+124)==0x80000fff and not m.get(mb+10*4)
m.put(BCMC,30);m.put(key+4,0);publish(m)
assert not m.get(mb+10*4) and m.get(key+20)==0xffffffff;cases+=1
print(f'Direct BCMC:{cases} actual EPP/MACID0/HIQ/GTK/sequence/header/RPQ/EQ and stale identity cases passed')

# Every valid port/MBID domain uses its own template, sequence and self MACID.
for role in list(range(8))+[0x10,0x20,0x30,0x40]:
 for macid in (0,41,127):
  for opened in (False,True):
   m=machine(cam=None if opened else 63,role=role,macid=macid)
   other=base+0x69000+10*28 if role!=0x40 else BCMC
   m.put(other,77);m.u.mem_write(mb+64,bytes.fromhex('020000999999'))
   choose(m);publish(m)
   assert m.get(m.seq+16)==0 and m.get(other)==77
   wd=PAGES+5*264*128;assert m.get(wd+8)>>24==macid and m.get(wd+32)&127==role
   length,address=struct.unpack('<HI',m.u.mem_read(wd+64,6));wire=bytes(m.u.mem_read(address,length&2047))
   assert wire[10:16]==bytes.fromhex('020000000001')
   assert m.get(base+0x20004)&(1<<24) and (m.get(base+0x20004)>>8&127)==macid
   finish(m)
   assert m.get(base+0x12000+macid*32)==0 and m.get(base+0x3e000+macid*16)==0
# Two VIFs in the same ingress batch retain distinct immutable recipes.
m=machine(role=1,macid=41);second=payload+2048
self2=bytes.fromhex('020000000022');group=e['group'];key=62<<16|1<<24;domain=2;token=24|domain<<27
m.put(base+0x68c00+20,24);m.put(base+0x68c00+20+16,99)
m.u.mem_write(base+0x69000+28,struct.pack('<5I',30,token,12,4|2<<24,key)+self2+bytes([63,0]))
m.put(base+0x1e900+62*40,12)
record=struct.pack('<II',7,1<<15)+group+bytes(2)+struct.pack('<4I',30,token,12,4|2<<24)+bytes(48)+self2+bytes(2)+struct.pack('<II',key,63<<24|1<<18|1<<13|1<<14|1<<15)
m.u.mem_write(table+16+10*96,record)
m.u.mem_write(second+64,struct.pack('>II',1<<31|266<<18,(len(m.frame)+8)<<15|19<<3)+bytes(8))
m.u.mem_write(second+80,bytes(m.frame));m.u.mem_write(e['epp']+8,struct.pack('<II',second|0x50,0));m.put(ni+0x7060,16)
m.call('ni_ingest',mb,base);assert m.get(ni+0x7160)==16 and m.get(mb+38*4)==2
assert m.call('ni_select',mb,base,requests)==1
assert m.call('ni_select',mb,base,requests+128)==1;m.put(mb+193*4,2)
# Closing the first domain rejects only that queued record. The other publishes.
m.put(m.bcmc,0);publish(m)
assert m.get(mb+10*4)==1 and m.get(base+0x68c00+20+16)==100 and m.get(m.seq+16)==4095
wd=PAGES+5*264*128;assert m.get(wd+8)>>24==63 and m.get(wd+32)&127==2
assert m.get(base+0x20004)&(1<<31) and not m.get(base+0x2000c)&(1<<31)
print('Multi-VIF BCMC:72 complete domain/self-MACID/open/GTK RPQ/EQ cases and shared-batch selective withdrawal passed')

if os.environ.get('RTL_TEST_DUAL_RECORD'):
 raw=Path(os.environ['RTL_TEST_DUAL_RECORD']).read_bytes();assert len(raw)==192
 for i in range(2):
  record=raw[i*96:(i+1)*96];epoch,control=struct.unpack_from('<II',record)
  gen,token,keygen,rate=struct.unpack_from('<4I',record,16);key,flags=struct.unpack_from('<II',record,88)
  role=rate>>24;macid=flags>>24;m=machine(cam=(key>>16)&127,keyid=key>>24&3,role=role,macid=macid)
  m.token=token;m.put(table,epoch);m.put(m.seq,token&((1<<27)-1))
  m.u.mem_write(m.bcmc,struct.pack('<5I',gen,token,keygen,rate,key)+record[80:86]+bytes([macid,0]))
  m.put(base+0x1e900+((key>>16)&127)*40,keygen)
  m.u.mem_write(table+16+9*96,record);m.u.mem_write(payload+80,record[8:14])
  m.u.mem_write(payload+68,struct.pack('>I',(len(m.frame)+8)<<15|((control>>16)&63)<<9|19<<3))
  choose(m);publish(m);wd=PAGES+5*264*128
  assert m.get(wd+8)>>24==macid and m.get(wd+32)&127==role;finish(m)
 print('Two actual complete-learner VIF records reached direct PE HIQ/GTK/sequence and RPQ/EQ')

# Full PE main staging must carry BCMC snapshot words28/29, not replace them
# with a unicast peer address when copying the used request prefix.
for role,macid in ((0,0),(3,41),(0x40,127)):
 m=machine(role=role,macid=macid)
 from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP
 m.u.mem_map(0x10020000,0x1000)
 m.u.mem_write(mb+64,bytes.fromhex('020000ffff00'))
 m.u.mem_write(base+0x68400+macid*8,bytes.fromhex('020000eeee00'))
 for i,v in ((3,1),(140,mb|0xa0000000),(142,1)):m.put(mb+i*4,v)
 m.u.reg_write(UC_MIPS_REG_A0,mb|0xa0000000);m.u.reg_write(UC_MIPS_REG_SP,0xa7e01ff0)
 m.u.emu_start(e['symbols']['pe_main'],0x9fc0fff0,count=24000)
 assert m.get(mb+10*4)==1 and m.get(m.seq+16)==0
 wd=PAGES+5*264*128;length,address=struct.unpack('<HI',m.u.mem_read(wd+64,6))
 assert bytes(m.u.mem_read(address+10,6))==bytes.fromhex('020000000001') and m.get(wd+8)>>24==macid
print('Full PE main carries three BCMC domain/address/self-MACID snapshots into hardware WDs')
