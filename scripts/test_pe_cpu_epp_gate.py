from pathlib import Path
root=Path(__file__).resolve().parents[1]
fixture=(root/"scripts/test_pe_rxq_backpressure.py").read_text()
exec(compile(fixture, str(root/"scripts/test_pe_rxq_backpressure.py"), "exec"))
# Model STATUS0 from current pointer occupancy, preserving other CPU bytes.
reads=[]
def status_model(r):
 def read(uc,access,at,size,value,data):
  at &= 0x1fffffff
  if at == 0x1000659c:
   active=sum(1<<c for c in range(8) if r.get(0x10007000+c*4)!=r.get(0x10007100+c*4))
   r.put(at,active | 0xffffff00)
  if at == 0x1000659c or 0x10007000<=at<0x10007020 or 0x10007100<=at<0x10007120: reads.append(at)
 r.u.hook_add(UC_HOOK_MEM_READ,read)
# Actual linked CPU EPP gate: congestion must not mutate payload, PN/SN or NI.
for cos in range(8):
 for used in (0,64,95,96,127):
  for pending in (0,1,8,80):
   r=reorder_peer();r.put(MB+90*4,pending);status_model(r)
   r.put(0x10007000+cos*4,(1000+used*8)&1023);r.put(0x10007100+cos*4,1000)
   at,raw=r.arrive(data_frame(0x123,2),True);r.capture();reads.clear();r.process()
   assert len(reads)==(3 if used else 1),(cos,used,pending,reads)
   blocked=used+pending+1>96
   assert r.get(MB+90*4)==pending+(not blocked)
   assert r.get(MB+175*4)==(0 if blocked else 1)
   assert not r.get(MB+94*4)
   if blocked:
    assert bytes(r.u.mem_read(at,len(raw)))==raw
    assert r.get(ST+1024)==1<<31|0x123
    r.put(0x10007000+cos*4,1000);r.put(VP+0x1c,pending);r.capture();r.process()
    assert r.get(MB+90*4)==pending+1 and r.get(MB+175*4)==1
print('CPU EPP gate: 160 occupancy/pending/COS/wrap cases retain untouched MPDU and resume after drain')

for mask in range(256):
 r=reorder_peer();status_model(r)
 for c in range(8):
  r.put(0x10007100+c*4,1000)
  r.put(0x10007000+c*4,(1000+(64+c if mask&(1<<c) else 0)*8)&1023)
 r.arrive(data_frame(0x123,2),True);r.capture();reads.clear();r.process()
 assert r.get(MB+90*4)==1 and len(reads)==1+2*mask.bit_count()
# An empty queue becoming congested must be checked on the next MPDU.
r=reorder_peer();status_model(r)
r.arrive(data_frame(0x123,2),True);r.call();assert r.get(MB+90*4)==1
r.put(0x10007014,96*8)
at,raw=r.arrive(data_frame(0x124,3),True);r.call()
assert r.get(MB+90*4)==1 and bytes(r.u.mem_read(at,len(raw)))==raw
print('CPU EPP status: all 256 active masks, isolated CPU0 byte and next-MPDU congestion; 1 empty / 3 one-active MMIO reads')
