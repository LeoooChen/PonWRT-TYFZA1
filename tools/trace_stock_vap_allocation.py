#!/usr/bin/env python3
"""Execute target role allocation through per-VAP MBID and shared WMM selection."""
import argparse,hashlib,json,struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
phl,com,sta=0x6000000,0x6010000,0x6030000
def boundary(name,v):
 if name=='memcmp':
  x=bytes(o.uc.mem_read(v[0],v[2]));y=bytes(o.uc.mem_read(v[1],v[2]));return 0 if x==y else 1
 if name=='_rtw_memcpy':o.uc.mem_write(v[0],bytes(o.uc.mem_read(v[1],v[2])));return v[0]
 if name=='phl_alloc_stainfo_sw':return sta
 if name in ('__local_bh_enable_ip','phl_mr_dump_role_info','phl_mr_dump_info','phl_init_role_cap','phl_init_protocol_cap','phl_wifi_role_start','printk'):return 0
 raise AssertionError((name,v))
o=ModuleOracle(a.module,['rtw_phl_wifi_role_alloc'],boundary,external_symbols={'vabits_actual':0x5007000})
o.word(0x5007000,48)
for at in (phl,com,sta):o.uc.mem_map(at,0x10000)
o.word(phl+4648,com)
o.uc.mem_write(com+0x3229,bytes([5,2]))
for i in range(9):o.uc.mem_write(com+i*1384+22,bytes([i]))
rows=[]
for i in range(8):
 address=o.scratch;out=address+64;o.uc.mem_write(address,bytes([2,0,0,0,0,i+1]))
 result=o.call('rtw_phl_wifi_role_alloc',phl,address,2 if i==0 else 3,255,out,0)
 role=o.unpack(out,'Q')[0]
 assert result==i and role==com+i*1384,(i,result,hex(role))
 port=o.unpack(role+35,'B')[0];wmm=o.unpack(role+23,'B')[0];mbid=o.unpack(role+1264,'B')[0]
 assert (port,wmm,mbid)==(0,0,i),(i,port,wmm,mbid)
 rows.append({'role':result,'port':port,'wmm':wmm,'mbid':mbid})
# Existing address returns the original interface instead of allocating again.
o.uc.mem_write(o.scratch,bytes([2,0,0,0,0,4]))
assert o.call('rtw_phl_wifi_role_alloc',phl,o.scratch,3,255,o.scratch+64,0)==3
p= {'sha256':hashlib.sha256(a.module.read_bytes()).hexdigest(),'rows':rows,'scope':'Actual target role allocator. Self station allocation/firmware startup and capability initialization modeled; no physical allocation.'}
a.output.write_text(json.dumps(p,indent=2)+'\n');print('Original allocator: root plus seven VAPs, independent MBID, shared port0/WMM0 and duplicate-address lookup PASS')
