#!/usr/bin/env python3
"""Compare NI's packet classification to the actual default AP tos_to_up."""
from pathlib import Path
import sys,ctypes,subprocess,tempfile,struct,itertools
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
D=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
def cb(n,a):raise AssertionError((n,a))
o=ModuleOracle(root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko',['tos_to_up'],cb)
A,S,K,P=0x6000000,0x6040000,0x6080000,0x60c0000
for a in [A,S,K,P]:o.uc.mem_map(a,0x40000)
o.word(K+0xc8,P);o.uc.mem_write(S+0x404,struct.pack('<I',1))
with tempfile.TemporaryDirectory(dir=root/'artifacts') as t:
 c=Path(t)/'c.c';so=Path(t)/'c.so';c.write_text('#include "qos.h"\nunsigned int tid(const unsigned char *p,unsigned int n){return rtl_ax_ethernet_tid(p,n); }\n')
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Werror','-O2','-I'+str(D),str(c),'-o',str(so)],check=True)
 lib=ctypes.CDLL(str(so));lib.tid.argtypes=[ctypes.c_void_p,ctypes.c_uint];lib.tid.restype=ctypes.c_uint
 count=0
 for proto,tos in itertools.product([0x0800,0x86dd,0x21,0x57],range(256)):
  payload=bytearray(40)
  if proto in [0x0800,0x21]:payload[0]=0x45;payload[1]=tos
  else:payload[0]=0x60 | tos>>4;payload[1]=(tos&15)<<4
  packet=bytes(12)+struct.pack('>H',proto if proto>255 else 0x8864)
  if proto<256:packet+=bytes.fromhex('110000010028')+struct.pack('>H',proto)
  packet+=payload
  o.uc.mem_write(P,packet);want=o.call('tos_to_up',A,S,K)
  assert lib.tid(packet,len(packet))==want,(proto,tos,want)
  count+=1
 assert lib.tid(bytes(15),15)==0 and lib.tid(bytes(14),14)==0
 print('PASS',count,'actual IPv4/IPv6/PPPoE DSCP+ECN cases; short non-IP frames')
