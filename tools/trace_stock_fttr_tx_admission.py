#!/usr/bin/env python3
"""Execute target TXSC admission and check the actual CCX parser time coverage."""
import argparse, hashlib, itertools, json, struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--wifi',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
observed=[]
def boundary(name,args):
 if name=='txsc_prepare_sc_entry':
  observed.append(o.unpack(args[2],'I')[0]);return 0
 raise AssertionError((name,args))
o=ModuleOracle(a.wifi,['txsc_add_sc_cache_entry','mac_ccxrpt_parsing','OnAction_wmm'],boundary)
adapter,frame,station,entry=[0x6000000+i*0x20000 for i in range(4)]
for address in (adapter,frame,station,entry):o.uc.mem_map(address,0x20000)
o.uc.mem_write(adapter+0x5b08,b'\1')
rows=[]
for protocol,qos,ampdu,software,fragments in itertools.product((0x800,0x86dd,0x8864,0x806),(0,1),(0,1),(0,1),(1,2)):
 data=bytearray(512)
 struct.pack_into('<Q',data,240,station)
 struct.pack_into('<H',data,52,protocol)
 data[36]=fragments;data[96]=qos;data[100]=ampdu;data[106]=software
 data[74]=2 # unicast RA; all encryption attributes remain zero
 o.uc.mem_write(frame,bytes(data));o.uc.mem_write(entry,bytes(704));observed.clear()
 o.call('txsc_add_sc_cache_entry',adapter,frame,entry)
 allowed=protocol in (0x800,0x86dd,0x8864) and qos and ampdu and not software and fragments==1
 assert observed==[2 if allowed else 0],(protocol,qos,ampdu,software,fragments,observed)
 rows.append(dict(protocol=protocol,qos=qos,ampdu=ampdu,software=software,fragments=fragments,admitted=bool(allowed)))
ccx=[]
for value in (0,1,127,255):
 raw=struct.pack('<10I',7<<16|2<<6|3<<8,value<<16|0x1234,0,9<<16|11<<10,0,0,0,0,0,0)
 o.uc.mem_write(frame,raw);o.uc.mem_write(entry,bytes(8))
 assert o.call('mac_ccxrpt_parsing',adapter,frame,entry)==0
 result=o.unpack(entry,'I')[0];ccx.append(result)
assert len(set(ccx))==1
assert o.call('OnAction_wmm',adapter,frame)==1
a.output.write_text(json.dumps(dict(sha256=hashlib.sha256(a.wifi.read_bytes()).hexdigest(),
 txsc=rows,ccx_outputs=ccx,wmm_action_return=1,
 scope='Actual target TXSC admission prefix; cache construction boundary captured. Unencrypted QoS/AMPDU data can enter cache. Actual CCX parser ignores accumulated-time byte; WMM action is a no-op. No hardware airtime unit, report completeness, throughput or full TXSC execution claim.'),indent=2)+'\n')
print(f'Original TXSC: {len(rows)} admission cases; CCX time-byte independence and WMM no-op verified')
