#!/usr/bin/env python3
"""Execute the board SWQ gates and compare the PE's ordinary AP policy."""
from pathlib import Path
import sys,struct,subprocess,ctypes,tempfile,itertools
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
D=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
M=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
def cb(n,a):
 if n=='txsc_amsdu_enqueue_tcp_ack':return 0
 if n in ['__local_bh_enable_ip','printk','_rtw_init_queue','tasklet_init','txsc_amsdu_swq_add_timeout_list']:return 0
 raise AssertionError((n,a))
o=ModuleOracle(M,['txsc_amsdu_enqueue','txsc_amsdu_deque_check','rtw_txsc_amsdu_timeout_init','txsc_init'],cb,{'vabits_actual':0x6100000,'jiffies':0x6100008})
A,V,S,P,K,B,E=0x6000000,0x6040000,0x6080000,0x60c0000,0x6100000,0x6140000,0x6180000
for a in [A,V,S,P,K,B,E]:o.uc.mem_map(a,0x40000)
def put(a,v,f='I'):o.uc.mem_write(a,struct.pack('<'+f,v))
put(K,48,'Q')
def setup(num,size,bw,tx,count,total):
 for a in [A,V,S,P,B,E]:o.uc.mem_write(a,bytes(0x40000))
 o.word(A+0xc0,V);o.word(S,P);o.word(E+8,S);o.word(E+32,B)
 put(A+0x5b18,1,'B');put(A+0x5b19,1,'B')
 put(S+0xa7cc,num,'B');put(S+0xa7d0,size);put(P+0x2c,bw);put(S+0x4ff8,tx<<10)
 put(S+0x8a3c,count-1);put(S+0x8a30,total-64);put(B+0x70,64)
 put(V+0x118,1,'B') # Suppress timer callback; gate itself is tx_mode independent.
with tempfile.TemporaryDirectory(dir=root/'artifacts') as tmp:
 c=Path(tmp)/'c.c';so=Path(tmp)/'c.so'
 c.write_text('#include "amsdu-queue.h"\nunsigned int ready(unsigned int p,unsigned int n,unsigned int b){return rtl_ax_amsdu_queue_ready(p,n,b); }\n')
 subprocess.run(['cc','-shared','-fPIC','-O2','-Wall','-Werror','-I'+str(D),str(c),'-o',str(so)],check=True)
 lib=ctypes.CDLL(str(so));cases=0
 for num,size,bw,tx,count in itertools.product([2,4,7],[3793,7433,10896],[2,3],[1300,1301],[1,2,4,7,8,9,127,128]):
  for total in [count*64,max(count*64,size-count*8-1),max(count*64,size-count*8)]:
   setup(num,size,bw,tx,count,total)
   assert o.call('txsc_amsdu_enqueue',A,E,K+16)==1
   want=o.unpack(K+16,'B')[0]==0
   policy=size | num<<16 | (1<<24 if bw==3 and tx>1300 else 0)
   got=bool(lib.ready(policy,count,total))
   assert got==want,(num,size,bw,tx,count,total,want,got)
   put(K+16,0,'B');cases+=1
 print('PASS',cases,'actual enqueue size/count/batch boundaries')
 # Exact default txsc mode and timer constants.
 o.call('txsc_init',A);assert o.unpack(A+0x5b19,'B')[0]==1
 o.call('rtw_txsc_amsdu_timeout_init',V)
 assert o.unpack(V+0x344,'II')==(10000,4000)
 # Batch drain tests the current aggregate count, not the initial128 trigger.
 for num,count in itertools.product([2,4,7],range(129)):
  setup(num,10896,3,1301,max(1,count),max(64,count*1514))
  put(S+0x8a3c,count);put(E+0x2aa,1,'B');put(E+0x2a9,0,'B')
  assert bool(o.call('txsc_amsdu_deque_check',A,A+0x5558,S,E))==(count>=num)
 print('PASS default size mode1, timers10000/4000us,387 batch drain boundaries')
