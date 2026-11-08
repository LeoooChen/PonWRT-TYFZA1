#!/usr/bin/env python3
"""Run original normal AP sleeping enqueue at its actual total queue limit."""
from pathlib import Path
import sys,struct,itertools
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
def cb(n,a):
 if n=='rtw_core_twt_sta_active':return 0
 if n=='rtw_tim_map_is_set':return 1
 if n in ['rtw_set_bit','__local_bh_enable_ip']:return 0
 raise AssertionError((n,a))
o=ModuleOracle(root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko',['os_tx_enqueue_for_sleeping_sta'],cb,{'vabits_actual':0x6100000})
A,V,S,P,K,B=0x6000000,0x6040000,0x6080000,0x60c0000,0x6100000,0x6140000
for a in [A,V,S,P,K,B]:o.uc.mem_map(a,0x40000)
def put(a,v,f='I'):o.uc.mem_write(a,struct.pack('<'+f,v))
put(K,48,'Q')
count=0
for total,peer,q in itertools.product([0,255,256,511,512,1023,2047,2048,2049],[0,256,512,2047],range(4)):
 for a in [A,V,S,P,B]:o.uc.mem_write(a,bytes(0x40000))
 o.word(A+0xc0,V);o.word(S,P);put(A+0xcc,0x10);put(S+0x400,4);put(A+0x9c88,2048);put(V+0x150,total)
 queue=S+0x60+q*32;o.word(queue,queue);o.word(queue+8,queue);put(queue+16,peer);put(S+0xe8,peer);put(B+0x2a,q,'B')
 result=o.call('os_tx_enqueue_for_sleeping_sta',A,S,B)
 assert result==(2 if total>=2048 else 1),(total,peer,q,result)
 assert o.unpack(V+0x150,'I')[0]==total+(total<2048)
 count+=1
print('PASS',count,'actual sleep queue admission cases; no per-peer256 limit')
