#!/usr/bin/env python3
"""Execute target 64-bit EPP decoder: Wi-Fi ID and internal-header length."""
import argparse,hashlib,json,struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def boundary(name,args):
 raise AssertionError((name,args))
o=ModuleOracle(a.module,['ca_ni_rx_napi_get_header_from_64bit_epp'],boundary,external_symbols={'memstart_addr':0x5007000,'printk':0x400ff00})
o.word(0x5007000,0);o.uc.mem_write(o.symbols['ca_ni_debug'][0],bytes(4))
base=o.scratch;cases=[]
for wifi_id in range(64):
 for length in (14,60,1500,1982,2048,0x3fff-16):
  o.uc.mem_write(base,bytes(0x1000));o.word(base,base+0x100)
  o.uc.mem_write(base+0x100,struct.pack('<II',0x6000050,(wifi_id<<14)|(length+16)))
  o.word(base+0xff00,base+0x80) # ninth argument lives at caller SP
  o.call('ca_ni_rx_napi_get_header_from_64bit_epp',base,base+0x20,base+0x28,base+0x30,base+0x38,0,base+0x40,base+0x48)
  actual=bytes(o.uc.mem_read(base+0x40,1))[0];compact=bytes(o.uc.mem_read(base+0x80,1))[0]
  assert compact==bool(wifi_id)
  if wifi_id:assert actual==wifi_id and o.unpack(base+0x48,'I')[0]==length
  else:assert o.unpack(base+0x28,'Q')[0]==0xffffff8006000040 and o.unpack(base+0x30,'Q')[0]==0xffffff8006000048
  cases.append({'id':wifi_id,'bytes':length,'direct':bool(compact)})
a.output.write_text(json.dumps({'sha256':hashlib.sha256(a.module.read_bytes()).hexdigest(),'function':'ca_ni_rx_napi_get_header_from_64bit_epp@.nictrx-sec+0x830','cases':cases,'scope':'Actual target decoder with constructed EPPs, no packet DMA or hardware timing.'},indent=2)+'\n')
print(f'Original EPP decoder: {len(cases)} ID0..63 and header/length cases passed')
