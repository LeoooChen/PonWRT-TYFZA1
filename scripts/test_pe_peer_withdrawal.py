#!/usr/bin/env python3
"""Execute selected NI unicast withdrawal before TXBD with actual BID/quota retirement."""
from pathlib import Path
import struct
root=Path(__file__).resolve().parents[1]
f=root/'scripts/test_fttr_ni_pe_drain.py'
e={'__file__':str(f),'__name__':'peer_withdrawal_fixture'}
exec(compile(f.read_text().split('cases = 0',1)[0],str(f),'exec'),e)
machine,base,mb,ni,bar,payload,table,requests,DRAIN=(e[n] for n in ('machine','base','mb','ni','bar','payload','table','requests','DRAIN'))

cases=0
for source in (0,6,7):
 for cause in ('route','peer-fence','global-fence'):
  for blocked in (False,True):
   m=machine(source=source,cookie=False);m.u.mem_write(payload+64+m.header+15,b'\0');m.ingest();m.put(bar+0xc438,10000)
   assert m.call('ni_select',mb,base,requests)==1
   assert m.get(base+0x3e010)==1 and m.get(base+0x20000)==payload
   m.put(mb+193*4,1)
   if blocked:
    m.put(bar+0x1058,1<<16);m.call('pe_tx_publish',mb,8,requests)
    assert not m.get(mb+194*4) and not m.get(mb+10*4)
   if cause=='route':m.put(base+0x11a04,0)
   elif cause=='peer-fence':m.put(mb+62*4,1)
   else:m.put(mb+62*4,128)
   m.call('pe_tx_publish',mb,8,requests)
   assert m.get(mb+194*4)==1 and not m.get(mb+10*4),(source,cause,blocked)
   assert m.get(mb+45*4)==1 and m.get(base+0x20004)&(1<<31)
   # One admitted MPDU retains its quota and full BID while EQ cannot accept it.
   m.call('ni_recycle',mb,base)
   assert m.get(base+0x3e010)==1 and m.get(base+0x20000)==payload
   assert m.get(DRAIN+8)==(source==6)
   m.put(ni+0x6340,1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
   assert not m.get(base+0x3e010) and not m.get(base+0x20000) and not m.get(DRAIN+8)
   assert m.get(base+0x34004)==2049 and not m.get(mb+30*4)
   # No hardware WP was ever taken; retirement cannot release a foreign tag.
   assert m.get(base+0x35000)==0xffffffff and m.get(base+0x34008)==0 and m.get(base+0x3400c)==0
   cases+=1
# Partial A-MSDU return retains one admitted request/quota through its last
# BID, even when its cancellation was caused by route removal, not FTTR close.
for tid,tos,ring in ((0,0,0),(1,32,1),(4,128,2),(6,192,3)):
 m=machine(cookie=False);m.u.mem_write(payload+64+m.header+15,bytes([tos]))
 m.put(base+0xe204,7935<<18)
 second=payload+2048;m.u.mem_write(second,bytes(m.u.mem_read(payload,2048)))
 m.u.mem_write(e['epp']+8,struct.pack('<II',second|0x50,(1<<14)|(len(m.frame)+16)));m.put(ni+0x7060,16)
 m.call('ni_ingest',mb,base);m.put(bar+0xc438,10000)
 assert m.call('ni_select',mb,base,requests)==2
 quota=base+0x3e000+(4+ring)*4
 assert m.get(quota)==1 and m.get(DRAIN+8)==2
 m.put(mb+193*4,1);m.put(base+0x11a04,0);m.call('pe_tx_publish',mb,8,requests)
 assert m.get(mb+194*4)==1 and not m.get(mb+10*4)
 m.put(ni+0x6340,1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
 assert m.get(base+0x20000)==second and m.get(quota)==1 and m.get(DRAIN+8)==1
 m.put(ni+0x6340,0);m.call('ni_recycle',mb,base)
 assert m.get(base+0x20000)==second and m.get(quota)==1
 m.put(ni+0x6340,1);m.call('ni_recycle',mb,base)
 assert not m.get(base+0x20000) and not m.get(quota) and not m.get(DRAIN+8)
 cases+=1
# A different peer's retirement cannot discard this admitted request. Once
# published, a later withdrawal cannot fabricate RPQ or reclaim its payload.
m=machine(cookie=False);m.u.mem_write(payload+64+m.header+15,b'\0');m.ingest();m.put(bar+0xc438,10000)
assert m.call('ni_select',mb,base,requests)==1
m.put(mb+193*4,1);m.put(mb+62*4,2);m.call('pe_tx_publish',mb,8,requests)
assert m.get(mb+10*4)==1 and m.get(mb+194*4)==1
m.put(base+0x11a04,0);m.put(mb+62*4,1);m.call('pe_tx_publish',mb,8,requests)
m.put(ni+0x6340,1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
assert not m.get(mb+45*4) and m.get(base+0x20000)==payload and m.get(base+0x3e010)==1
# Only the hardware report can complete the published WP.
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP
m.u.mem_map(0x10020000,0x1000)
tag=m.get(e['PAGES']+56)&4095
raw=struct.pack('<II',24|0xc000|1<<16,7<<24|4)+bytes(12)+struct.pack('<I',1|tag<<16)
m.u.mem_write(e['REPORTS'],raw);m.put(bar+0x121c,1<<16)
for index,value in ((3,1),(140,mb|0xa0000000),(142,1)):m.put(mb+index*4,value)
m.u.reg_write(UC_MIPS_REG_A0,mb|0xa0000000);m.u.reg_write(UC_MIPS_REG_SP,0xa7e01ff0)
m.u.emu_start(e['env']['symbols']['pe_main'],0x9fc0fff0,count=20000)
assert not m.get(base+0x20000) and not m.get(base+0x3e010) and not m.get(DRAIN+8)
assert not m.get(base+0x40000+tag*4)
cases+=1
# BCMC is independent of per-station fences, but global retirement still
# cancels an already selected HIQ request before hardware publication.
f=root/'scripts/test_pe_bcmc.py';b={'__file__':str(f),'__name__':'bcmc_withdrawal_fixture'}
exec(compile(f.read_text().split("if os.environ.get('RTL_TEST_AP_GROUP_RECORD'):",1)[0],str(f),'exec'),b)
for fence in (1,128):
 m=b['machine']();b['choose'](m);m.put(mb+62*4,fence);b['publish'](m)
 assert m.get(mb+194*4)==1 and m.get(mb+10*4)==(fence!=128)
 if fence==128:
  m.put(ni+0x6340,1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
  assert not m.get(base+0x20000) and not m.get(mb+30*4)
 else:b['finish'](m)
 assert not any(m.get(base+0x3e000+i*4) for i in range(4))
 cases+=1
print(f'NI peer withdrawal: {cases} selected/credit-stalled, source0/6/7, route/fence and quota/BID retirement cases passed')
