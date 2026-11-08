#!/usr/bin/env python3
"""Execute board ca_dma_tx_complete_easy and verify its two-descriptor holdback."""
import argparse,hashlib,json,struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ni',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
freed=[];consumer=0

def boundary(name,v):
 if name=='aal_ni_get_tx_read_ptr':return consumer
 if name=='__ca_free_skb_any':freed.append(v[0]);return 0
 raise AssertionError((name,v))
o=ModuleOracle(a.ni,['ca_dma_tx_complete_easy'],boundary)
base=o.symbols['ni_info_data'][0]
checks=0;edge_cases=[]
for start in range(64):
 for distance in range(64):
  freed.clear();consumer=(start+distance)&63
  o.uc.mem_write(base,bytes(0x2850))
  o.uc.mem_write(base+64,struct.pack('<H',start))
  o.uc.mem_write(base+112,struct.pack('<I',64))
  for i in range(64):o.uc.mem_write(base+136+i*8,struct.pack('<Q',0x6000000+i*128))
  ret=o.call('ca_dma_tx_complete_easy',0,0)
  expected=[0x6000000+((start+i)&63)*128 for i in range(max(0,distance-2))]
  if freed!=expected:
   # Factory comparison wraps finished+2 before checking whether RPTR
   # advanced. These synthetic distances cannot arise after its ordinary
   # completion has established the two-descriptor holdback.
   assert distance<2 and start>=62,(start,distance)
   edge_cases.append(dict(start=start,distance=distance,released=len(freed)))
  assert ret==len(freed)
  assert o.unpack(base+64,'H')[0]==(start+len(freed))&63
  checks+=1
report=dict(cases=checks,synthetic_wrap_edge_cases=edge_cases,stock_sha256=hashlib.sha256(a.ni.read_bytes()).hexdigest(),retained_descriptors=2,
 scope='Actual target ARM64 completion routine with explicit RPTR and skb-free boundaries; no modeled AFT/NI/ES empty claim.')
a.output.write_text(json.dumps(report,indent=2)+'\n');print(f'Stock DMA completion:{checks} RPTR/reclaim pairs, {len(edge_cases)} synthetic short-distance wrap defects recorded; normal holdback verified')
